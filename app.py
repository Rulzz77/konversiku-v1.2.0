from __future__ import annotations

import os
import re
import shutil
import socket
import tempfile
import threading
import subprocess
import hmac
import secrets
import time
import json
import logging
import zipfile
from pathlib import Path
from urllib.parse import urlparse, urljoin

import imageio_ffmpeg
import yt_dlp
from flask import Flask, jsonify, render_template, request, send_file, session, redirect, url_for
from werkzeug.utils import secure_filename
import requests

APP_NAME = "KonversiKu"
APP_VERSION = "1.2.1"
logger = logging.getLogger("konversiku")
BASE_DIR = Path(__file__).resolve().parent
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))  # 100 MB on hosted deployment by default

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("RENDER", "").lower() == "true",
)
AUTH_REQUIRED = os.environ.get("KONVERSIKU_REQUIRE_AUTH", "false").lower() in {"1", "true", "yes"}
ACCESS_CODE = os.environ.get("KONVERSIKU_ACCESS_CODE", "").strip()


@app.before_request
def protect_hosted_instance():
    # The health endpoint must remain reachable by the hosting provider.
    if request.endpoint in {"login", "health", "static"} or not AUTH_REQUIRED:
        return None
    if not ACCESS_CODE:
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Layanan belum dikonfigurasi: atur KONVERSIKU_ACCESS_CODE pada hosting."}), 503
        return render_template("setup_required.html"), 503
    if session.get("konversiku_authenticated"):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "Kamu perlu masuk ke KonversiKu terlebih dahulu."}), 401
    return redirect(url_for("login", next=request.full_path))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not AUTH_REQUIRED:
        return redirect(url_for("home"))
    if not ACCESS_CODE:
        return render_template("setup_required.html"), 503
    error = ""
    if request.method == "POST":
        submitted = request.form.get("access_code", "")
        if hmac.compare_digest(submitted, ACCESS_CODE):
            session.clear()
            session["konversiku_authenticated"] = True
            session.permanent = True
            target = request.args.get("next", "/")
            if not target.startswith("/") or target.startswith("//"):
                target = "/"
            return redirect(target)
        error = "Kode akses salah. Coba lagi."
    return render_template("login.html", error=error)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# A deliberate allow-list: this app only accepts links from common media platforms.
ALLOWED_HOSTS = {
    "tiktok.com", "instagram.com", "facebook.com", "fb.watch",
    "youtube.com", "youtu.be", "x.com", "twitter.com", "pinterest.com",
    "pin.it", "reddit.com", "vimeo.com", "dailymotion.com",
    "threads.net", "tumblr.com", "soundcloud.com",
}

PLATFORM_NAMES = {
    "tiktok.com": "TikTok", "instagram.com": "Instagram", "facebook.com": "Facebook",
    "fb.watch": "Facebook", "youtube.com": "YouTube", "youtu.be": "YouTube",
    "x.com": "X", "twitter.com": "X / Twitter", "pinterest.com": "Pinterest",
    "pin.it": "Pinterest", "reddit.com": "Reddit", "vimeo.com": "Vimeo",
    "dailymotion.com": "Dailymotion", "threads.net": "Threads",
    "tumblr.com": "Tumblr", "soundcloud.com": "SoundCloud",
}

FORMAT_EXTENSIONS = {
    "mp3": ".mp3",
    "wav": ".wav",
    "m4a": ".m4a",
    "mp4": ".mp4",
    "webm": ".webm",
}

# Prevent simultaneous FFmpeg/yt-dlp jobs from exhausting the local machine.
JOB_LIMIT = threading.Semaphore(2)
DOWNLOAD_JOBS: dict[str, dict] = {}
DOWNLOAD_JOBS_LOCK = threading.Lock()
MAX_TIKTOK_PHOTOS = 30
MAX_TIKTOK_PHOTO_BYTES = 12 * 1024 * 1024
MAX_TIKTOK_PHOTO_TOTAL_BYTES = 80 * 1024 * 1024


def ffmpeg_path() -> str:
    """Use the FFmpeg executable supplied by imageio-ffmpeg."""
    return imageio_ffmpeg.get_ffmpeg_exe()


def exception_detail(exc: BaseException, limit: int = 500) -> str:
    """Return a useful, bounded message even when an exception has no text."""
    message = str(exc).strip()
    if not message:
        message = f"{type(exc).__name__}: proses berhenti tanpa memberikan detail error."
    else:
        message = message.splitlines()[-1].strip()
    return message[:limit] or f"{type(exc).__name__}: proses gagal tanpa detail."


def validated_media_url(raw_url: str) -> tuple[str, str]:
    raw_url = (raw_url or "").strip()
    if not raw_url:
        raise ValueError("Tempel tautan terlebih dahulu.")
    if len(raw_url) > 4096:
        raise ValueError("Tautan terlalu panjang.")

    parsed = urlparse(raw_url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("Tautan harus diawali http:// atau https://.")

    host = parsed.hostname.lower().rstrip(".")
    matched = None
    for allowed in ALLOWED_HOSTS:
        if host == allowed or host.endswith("." + allowed):
            matched = allowed
            break
    if not matched:
        raise ValueError("Platform ini belum masuk daftar dukungan. Coba TikTok, Instagram, Facebook, YouTube, X, Pinterest, Reddit, Vimeo, atau SoundCloud.")

    # Avoid credential-in-URL tricks such as https://allowed.example@127.0.0.1.
    if parsed.username or parsed.password:
        raise ValueError("Tautan dengan nama pengguna/kata sandi di URL tidak diizinkan.")

    platform = PLATFORM_NAMES.get(matched, matched.title())
    return raw_url, platform


def is_tiktok_photo_url(url: str) -> bool:
    """Return True for public TikTok photo/slideshow permalink shapes."""
    try:
        parsed = urlparse(url or "")
    except (TypeError, ValueError):
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return (
        (host == "tiktok.com" or host.endswith(".tiktok.com"))
        and re.match(r"^/@[^/]+/photo/\d+/?$", parsed.path, flags=re.IGNORECASE) is not None
    )


def tiktok_photo_audio_url(url: str) -> str:
    """Work around yt-dlp's unsupported /photo/ route for audio-only extraction.

    yt-dlp can extract the attached soundtrack through the corresponding /video/ route
    for some public TikTok photo posts. This helper does not download the slideshow images.
    """
    parsed = urlparse(url)
    if not is_tiktok_photo_url(url):
        return url
    path = re.sub(r"/photo/(\d+)/?$", r"/video/\1", parsed.path, count=1, flags=re.IGNORECASE)
    return parsed._replace(path=path).geturl()


def tiktok_photo_id(url: str) -> str | None:
    match = re.search(r"/@[^/]+/photo/(\d+)", urlparse(url or "").path, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _first_url_list(value) -> list[str]:
    """Read TikTok's several image URL-list spellings from a nested image object."""
    if isinstance(value, str):
        return [value] if value.startswith(("https://", "http://")) else []
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(_first_url_list(item))
        return result
    if not isinstance(value, dict):
        return []
    for key in ("urlList", "url_list", "url_list_v2", "urls", "url"):
        if key in value:
            found = _first_url_list(value[key])
            if found:
                return found
    result = []
    for key in ("imageURL", "imageUrl", "displayImage", "display_image", "originURL", "origin_url", "cover", "urlList"):
        if key in value:
            result.extend(_first_url_list(value[key]))
    return result


def _is_allowed_tiktok_image_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not host:
            return False
        allowed_suffixes = (
            "tiktokcdn.com", "tiktokcdn-us.com", "tiktokcdn-eu.com", "tiktokcdn-in.com",
            "byteoversea.com", "ibytedtos.com", "ibyteimg.com", "pstatp.com",
            "byteimg.com", "bytedance.com", "muscdn.com", "tiktok.com",
        )
        return any(host == suffix or host.endswith("." + suffix) for suffix in allowed_suffixes)
    except (ValueError, TypeError):
        return False


def _image_urls_from_item(item: dict) -> list[str]:
    image_post = (
        item.get("imagePost") or item.get("image_post_info") or item.get("image_post")
        or item.get("imageInfo") or item.get("image_info") or item.get("images") or item.get("image_list")
    )
    if isinstance(image_post, dict):
        image_list = image_post.get("images") or image_post.get("image_list") or image_post.get("imageList") or []
    elif isinstance(image_post, list):
        image_list = image_post
    else:
        image_list = []
    urls: list[str] = []
    for image in image_list:
        if not isinstance(image, dict):
            continue
        candidates = []
        for key in ("imageURL", "imageUrl", "image_url", "displayImage", "display_image", "originURL", "origin_url", "downloadURL", "download_url", "urlList", "url_list"):
            if key in image:
                candidates.extend(_first_url_list(image[key]))
        unique = []
        for candidate in candidates:
            if candidate not in unique and _is_allowed_tiktok_image_url(candidate):
                unique.append(candidate)
        if unique:
            # Prefer the first CDN rendition; TikTok often lists equivalent-size alternatives.
            urls.append(unique[0])
    return urls[:MAX_TIKTOK_PHOTOS]


def _find_tiktok_item(payload, wanted_id: str) -> dict | None:
    if not isinstance(payload, (dict, list)):
        return None
    for node in _walk_dicts(payload):
        node_id = str(node.get("id") or node.get("aweme_id") or node.get("itemId") or "")
        has_images = any(key in node for key in ("imagePost", "image_post_info", "image_post", "imageInfo", "image_info"))
        if node_id == wanted_id and (has_images or "video" in node or "music" in node):
            return node
    for node in _walk_dicts(payload):
        if any(key in node for key in ("imagePost", "image_post_info", "image_post", "imageInfo", "image_info")):
            return node
    return None


def _photo_details_from_payloads(payloads: list, post_id: str):
    for payload in payloads:
        item = _find_tiktok_item(payload, post_id)
        if not item:
            continue
        images = _image_urls_from_item(item)
        if images:
            author = item.get("author") if isinstance(item.get("author"), dict) else {}
            music = item.get("music") if isinstance(item.get("music"), dict) else {}
            image_post = item.get("imagePost") if isinstance(item.get("imagePost"), dict) else {}
            legacy_image_post = item.get("image_post_info") if isinstance(item.get("image_post_info"), dict) else {}
            image_post_music = image_post.get("music") if isinstance(image_post.get("music"), dict) else {}
            legacy_post_music = legacy_image_post.get("music") if isinstance(legacy_image_post.get("music"), dict) else {}
            duration = music.get("duration") or image_post_music.get("duration") or legacy_post_music.get("duration")
            return {
                "id": post_id,
                "images": images,
                "title": str(item.get("desc") or item.get("title") or f"TikTok foto slide {post_id}")[:300],
                "uploader": str(author.get("uniqueId") or author.get("nickname") or "Pembuat tidak diketahui")[:180],
                "duration": duration,
            }
    return None


def resolve_tiktok_photo_post(url: str) -> dict:
    """Resolve public TikTok slideshow images from public page metadata, with bounded requests."""
    post_id = tiktok_photo_id(url)
    if not post_id:
        raise ValueError("Tautan ini bukan tautan foto-slide TikTok yang valid.")
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"),
        "Referer": "https://www.tiktok.com/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
    }
    payloads = []
    errors = []
    # Public item-detail endpoint is usually the leanest route to a slideshow's image list.
    try:
        response = requests.get(
            "https://www.tiktok.com/api/item/detail/",
            params={
                "itemId": post_id, "aid": "1988", "lang": "en", "app_name": "tiktok_web",
                "channel": "tiktok_web", "device_platform": "web_pc", "region": "US",
            }, headers=headers, timeout=(5, 12), allow_redirects=True,
        )
        if response.ok:
            try:
                payloads.append(response.json())
            except (ValueError, json.JSONDecodeError):
                pass
        else:
            errors.append(f"item detail HTTP {response.status_code}")
    except requests.RequestException as exc:
        errors.append(type(exc).__name__)
    resolved = _photo_details_from_payloads(payloads, post_id)
    if resolved:
        return resolved

    # Fallback: TikTok sometimes embeds the item structure in the public /video/ page.
    try:
        video_url = tiktok_photo_audio_url(url)
        response = requests.get(video_url, headers=headers, timeout=(5, 12), allow_redirects=True)
        if response.ok:
            html = response.text[:10_000_000]
            for script_id in ("__UNIVERSAL_DATA_FOR_REHYDRATION__", "SIGI_STATE"):
                match = re.search(
                    rf'<script[^>]+id=["\']{re.escape(script_id)}["\'][^>]*>(.*?)</script>',
                    html, flags=re.IGNORECASE | re.DOTALL,
                )
                if match:
                    try:
                        payloads.append(json.loads(match.group(1)))
                    except (ValueError, json.JSONDecodeError):
                        pass
        else:
            errors.append(f"public page HTTP {response.status_code}")
    except requests.RequestException as exc:
        errors.append(type(exc).__name__)
    resolved = _photo_details_from_payloads(payloads, post_id)
    if resolved:
        return resolved

    # Last fallback for posts where the /video/ page is app-gated.
    try:
        mobile_headers = dict(headers)
        mobile_headers["User-Agent"] = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        response = requests.get(url, headers=mobile_headers, timeout=(5, 12), allow_redirects=True)
        if response.ok:
            html = response.text[:10_000_000]
            for script_id in ("__UNIVERSAL_DATA_FOR_REHYDRATION__", "SIGI_STATE"):
                match = re.search(
                    rf'<script[^>]+id=["\']{re.escape(script_id)}["\'][^>]*>(.*?)</script>',
                    html, flags=re.IGNORECASE | re.DOTALL,
                )
                if match:
                    try:
                        payloads.append(json.loads(match.group(1)))
                    except (ValueError, json.JSONDecodeError):
                        pass
    except requests.RequestException as exc:
        errors.append(type(exc).__name__)
    resolved = _photo_details_from_payloads(payloads, post_id)
    if resolved:
        return resolved

    raise RuntimeError(
        "TikTok tidak memberikan daftar foto slide untuk tautan ini. Pastikan postingan publik dan tidak dibatasi login. "
        "".join(errors[:2])
    )


def _update_job(job_id: str | None, **updates):
    if not job_id:
        return
    with DOWNLOAD_JOBS_LOCK:
        job = DOWNLOAD_JOBS.get(job_id)
        if job:
            job.update(updates)
            job["updated"] = time.time()


def _job_progress_hook(job_id: str | None, start: float = 5, span: float = 78, label: str = "Mengunduh media"):
    def hook(data):
        status = data.get("status")
        if status == "downloading":
            downloaded = data.get("downloaded_bytes") or 0
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            ratio = min(1.0, downloaded / total) if total else None
            if ratio is None and data.get("_percent_str"):
                try:
                    ratio = min(1.0, max(0.0, float(str(data["_percent_str"]).replace("%", "").strip()) / 100.0))
                except (TypeError, ValueError):
                    ratio = None
            if ratio is None and data.get("fragment_count"):
                try:
                    ratio = min(1.0, max(0.0, float(data.get("fragment_index", 0)) / float(data["fragment_count"])))
                except (TypeError, ValueError, ZeroDivisionError):
                    ratio = None
            speed = data.get("speed")
            eta = data.get("eta")
            percent = round(start + span * ratio, 1) if ratio is not None else None
            details = label
            if speed:
                details += f" · {speed / (1024 * 1024):.2f} MB/detik"
            if eta is not None:
                details += f" · sisa sekitar {int(eta)} dtk"
            updates = {"text": details, "stage": "download"}
            if percent is not None:
                updates["percent"] = max(1, min(95, percent))
            _update_job(job_id, **updates)
        elif status == "finished":
            _update_job(job_id, percent=max(start + span, 80), text="Unduhan selesai, menyiapkan file...", stage="processing")
    return hook


def _safe_image_extension(url: str, content_type: str = "") -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".avif"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    mime = (content_type or "").split(";", 1)[0].lower()
    return {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/avif": ".avif"}.get(mime, ".jpg")


def _download_photo_image(url: str, path: Path, job_id: str | None = None,
                          progress_start: float = 5, progress_span: float = 60,
                          label: str = "Mengunduh foto") -> int:
    current_url = url
    response = None
    for _redirect_attempt in range(4):
        if not _is_allowed_tiktok_image_url(current_url):
            raise RuntimeError("Alamat gambar bukan CDN TikTok yang diizinkan.")
        response = requests.get(
            current_url,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.tiktok.com/"},
            stream=True, timeout=(5, 20), allow_redirects=False,
        )
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise RuntimeError("URL foto mengarahkan ke lokasi yang tidak valid.")
            current_url = urljoin(current_url, location)
            continue
        break
    if response is None:
        raise RuntimeError("Gagal membuka gambar.")
    try:
        response.raise_for_status()
        if not _is_allowed_tiktok_image_url(response.url):
            raise RuntimeError("TikTok mengarahkan unduhan ke alamat gambar yang tidak diizinkan.")
        content_type = response.headers.get("Content-Type", "")
        if not content_type.lower().startswith("image/"):
            raise RuntimeError("Tautan foto tidak mengembalikan file gambar.")
        extension = _safe_image_extension(response.url, content_type)
        if path.suffix.lower() != extension:
            path = path.with_suffix(extension)
        expected = response.headers.get("Content-Length")
        try:
            expected_bytes = int(expected) if expected else None
        except (TypeError, ValueError):
            expected_bytes = None
        total = 0
        with path.open("wb") as target:
            for chunk in response.iter_content(128 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_TIKTOK_PHOTO_BYTES:
                    raise RuntimeError("Salah satu foto melewati batas 12 MB.")
                target.write(chunk)
                ratio = min(1.0, total / expected_bytes) if expected_bytes and expected_bytes > 0 else None
                updates = {"text": f"{label} · {total / (1024 * 1024):.2f} MB", "stage": "photos"}
                if ratio is not None and job_id:
                    updates["percent"] = round(progress_start + progress_span * ratio, 1)
                _update_job(job_id, **updates)
        if total == 0:
            raise RuntimeError("Foto yang diterima kosong.")
        return total
    finally:
        response.close()


def _download_photo_set(url: str, temp_dir: Path, job_id: str, indices=None, details=None, progress_end: float = 70) -> list[Path]:
    details = details or resolve_tiktok_photo_post(url)
    all_urls = details["images"]
    if not all_urls:
        raise RuntimeError("Tidak ada foto di postingan ini.")
    if indices is None:
        selected = list(enumerate(all_urls))
    else:
        selected = [(int(i), all_urls[int(i)]) for i in indices]
    if len(selected) > MAX_TIKTOK_PHOTOS:
        raise RuntimeError(f"Postingan memiliki terlalu banyak foto; batasnya {MAX_TIKTOK_PHOTOS} foto.")
    downloaded: list[Path] = []
    total_bytes = 0
    for position, (original_index, image_url) in enumerate(selected, start=1):
        progress_range = max(1, progress_end - 5)
        progress_start = 5 + progress_range * (position - 1) / max(1, len(selected))
        progress_span = progress_range / max(1, len(selected))
        _update_job(job_id, text=f"Mengunduh foto {position} dari {len(selected)}...", stage="photos", percent=round(progress_start, 1))
        dest = temp_dir / f"foto-{original_index + 1:02d}.jpg"
        byte_count = _download_photo_image(
            image_url, dest, job_id, progress_start, progress_span,
            f"Mengunduh foto {position} dari {len(selected)}",
        )
        actual = dest if dest.exists() else next(temp_dir.glob(f"foto-{original_index + 1:02d}.*"), dest)
        total_bytes += byte_count
        if total_bytes > MAX_TIKTOK_PHOTO_TOTAL_BYTES:
            raise RuntimeError("Total ukuran foto melewati batas 80 MB.")
        downloaded.append(actual)
        _update_job(job_id, percent=min(progress_end, 5 + progress_range * position / max(1, len(selected))), text=f"Foto {position} dari {len(selected)} selesai diunduh.", stage="photos")
    return downloaded


def _download_photo_audio(url: str, temp_dir: Path, job_id: str):
    audio_dir = temp_dir / "audio"
    audio_dir.mkdir(exist_ok=True)
    audio_url = tiktok_photo_audio_url(url)
    opts = make_ydl_options(
        outtmpl=str(audio_dir / "soundtrack.%(ext)s"),
        format="bestaudio/best",
        progress_hooks=[_job_progress_hook(job_id, 47, 21, "Mengunduh lagu postingan")],
    )
    info = None
    try:
        with JOB_LIMIT:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(audio_url, download=False)
                ydl.download([audio_url])
    except Exception as exc:
        _update_job(job_id, text="Lagu tidak berhasil diambil; video akan dibuat tanpa musik.", note="Lagu tidak dapat diambil dari postingan ini, jadi video dibuat tanpa audio.")
        return None, None
    candidates = [p for p in audio_dir.iterdir() if p.is_file() and not p.name.endswith((".part", ".ytdl"))]
    candidates = [p for p in candidates if p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".json"}]
    if not candidates:
        _update_job(job_id, note="Lagu tidak berhasil diambil dari postingan ini, jadi video dibuat tanpa audio.")
        return None, (info or {}).get("duration") if isinstance(info, dict) else None
    return max(candidates, key=lambda p: p.stat().st_size), ((info or {}).get("duration") if isinstance(info, dict) else None)


def _build_slideshow_video(image_paths: list[Path], output_path: Path, audio_path: Path | None, audio_duration, job_id: str):
    if not image_paths:
        raise RuntimeError("Tidak ada foto yang bisa dijadikan video.")
    normal_dir = output_path.parent / "normalized"
    normal_dir.mkdir(exist_ok=True)
    normalized = []
    for index, source in enumerate(image_paths, start=1):
        target = normal_dir / f"slide-{index:03d}.jpg"
        _update_job(job_id, percent=70 + 12 * (index - 1) / max(1, len(image_paths)), text=f"Menyiapkan foto {index} dari {len(image_paths)} untuk video...", stage="processing")
        command = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                   "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1",
                   "-frames:v", "1", str(target)]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if completed.returncode != 0 or not target.exists() or target.stat().st_size == 0:
            raise RuntimeError("Salah satu foto tidak bisa disiapkan untuk MP4.")
        normalized.append(target)
    per_slide = (float(audio_duration) / len(normalized)) if audio_path and audio_duration and audio_duration > 0 else 2.5
    per_slide = max(0.5, min(10.0, per_slide))
    manifest = output_path.parent / "slides.txt"
    def quote_concat(path: Path) -> str:
        return str(path.resolve()).replace("'", "'\\''")
    lines = []
    for image in normalized:
        lines.extend([f"file '{quote_concat(image)}'", f"duration {per_slide:.3f}"])
    lines.append(f"file '{quote_concat(normalized[-1])}'")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _update_job(job_id, percent=84, text="Menyusun video MP4 dari foto slide...", stage="encoding")
    command = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(manifest)]
    if audio_path and audio_path.exists():
        command += ["-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0"]
    else:
        command += ["-map", "0:v:0", "-an"]
    command += ["-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
    if audio_path and audio_path.exists():
        command += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    expected_duration = max(0.5, per_slide * len(normalized))
    command += ["-progress", "pipe:1", "-nostats", "-movflags", "+faststart", str(output_path)]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        kill_timer = threading.Timer(60 * 30, process.kill)
        kill_timer.daemon = True
        kill_timer.start()
        try:
            if process.stdout:
                for line in process.stdout:
                    line = line.strip()
                    if line.startswith("out_time_ms="):
                        try:
                            elapsed_seconds = int(line.split("=", 1)[1]) / 1_000_000
                            ratio = min(1.0, elapsed_seconds / expected_duration)
                            _update_job(job_id, percent=round(84 + 13 * ratio, 1),
                                        text=f"Menyusun video MP4... {round(ratio * 100)}%", stage="encoding")
                        except (ValueError, ZeroDivisionError):
                            pass
            stderr_text = process.stderr.read() if process.stderr else ""
            return_code = process.wait(timeout=60 * 30)
        finally:
            kill_timer.cancel()
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
        raise
    if return_code != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        lines = (stderr_text or "FFmpeg tidak bisa membuat video dari foto.").strip().splitlines()
        raise RuntimeError(lines[-1][:300] if lines else "FFmpeg tidak bisa membuat video dari foto.")


def make_ydl_options(**extra):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 2,
        "fragment_retries": 2,
        "ffmpeg_location": ffmpeg_path(),
        "extractor_retries": 1,
    }
    opts.update(extra)
    return opts


def readable_duration(seconds):
    if not seconds:
        return "Durasi tidak tersedia"
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "Durasi tidak tersedia"
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def clean_download_name(value: str, fallback: str = "konversiku") -> str:
    value = secure_filename(value or "")
    value = re.sub(r"\.+", ".", value).strip("._-")
    return (value[:140] or fallback)


def send_temp_file(path: Path, temp_dir: str, mime: str | None = None):
    response = send_file(
        path,
        as_attachment=True,
        download_name=path.name,
        mimetype=mime,
        conditional=True,
    )

    def cleanup():
        shutil.rmtree(temp_dir, ignore_errors=True)

    response.call_on_close(cleanup)
    return response


@app.get("/")
def home():
    return render_template("index.html", app_name=APP_NAME, auth_required=AUTH_REQUIRED)


@app.get("/api/health")
def health():
    try:
        bin_path = ffmpeg_path()
        ffmpeg_ok = os.path.isfile(bin_path)
    except Exception:
        bin_path = ""
        ffmpeg_ok = False
    deno_path = shutil.which("deno")
    try:
        ytdlp_version = getattr(getattr(yt_dlp, "version", None), "__version__", "unknown")
    except Exception:
        ytdlp_version = "unknown"
    return jsonify({
        "ok": True,
        "ffmpeg": ffmpeg_ok,
        "deno": bool(deno_path),
        "yt_dlp": ytdlp_version,
        "version": APP_VERSION,
    })


@app.post("/api/info")
def get_info():
    data = request.get_json(silent=True) or {}
    try:
        url, platform = validated_media_url(data.get("url", ""))
        photo_slideshow = platform == "TikTok" and is_tiktok_photo_url(url)
        photo_details = None
        info = None
        if photo_slideshow:
            try:
                photo_details = resolve_tiktok_photo_post(url)
            except Exception:
                photo_details = None
        extract_url = tiktok_photo_audio_url(url) if photo_slideshow else url
        try:
            with JOB_LIMIT:
                with yt_dlp.YoutubeDL(make_ydl_options(skip_download=True, extract_flat=False)) as ydl:
                    info = ydl.extract_info(extract_url, download=False)
        except Exception:
            if not photo_slideshow or not photo_details:
                raise
        if info and info.get("_type") in {"playlist", "multi_video"}:
            entries = info.get("entries") or []
            info = next((entry for entry in entries if entry), None)
        if not info and not photo_details:
            raise ValueError("Tidak bisa membaca detail konten ini.")
        title = (photo_details or {}).get("title") or (info or {}).get("title") or "Konten tanpa judul"
        uploader = (photo_details or {}).get("uploader") or (info or {}).get("uploader") or (info or {}).get("channel") or "Pembuat tidak diketahui"
        thumbnail = (info or {}).get("thumbnail") or ((photo_details or {}).get("images") or [""])[0]
        return jsonify({
            "ok": True,
            "platform": platform,
            "title": str(title)[:300],
            "uploader": str(uploader)[:180],
            "duration": readable_duration((info or {}).get("duration") or (photo_details or {}).get("duration")),
            "thumbnail": thumbnail or "",
            "webpage_url": url if photo_slideshow else ((info or {}).get("webpage_url") or url),
            "post_type": "tiktok_photo_slideshow" if photo_slideshow else "video_or_media",
            "image_count": len((photo_details or {}).get("images") or []),
            "images": (photo_details or {}).get("images") or [],
            "images_available": bool((photo_details or {}).get("images")),
            "content_note": (
                "Foto slide terdeteksi. Bisa dicoba dibuat MP4, diunduh sebagai ZIP, atau disimpan satu per satu."
                if photo_slideshow else ""
            ),
        })
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        message = str(exc).strip().splitlines()
        detail = message[-1][:350] if message else f"{type(exc).__name__}: gagal membaca tautan tanpa detail tambahan."
        if is_tiktok_photo_url((data or {}).get("url", "")):
            friendly = "TikTok foto slide terdeteksi, tetapi detail postingan tidak dapat dibaca. Pastikan postingan publik dan tidak dibatasi login. "
        else:
            friendly = "Konten tidak bisa dibaca. Pastikan tautan publik dan masih aktif, lalu coba lagi. "
        return jsonify({"ok": False, "error": friendly + detail}), 502


@app.post("/api/download")
def download_media():
    data = request.get_json(silent=True) or {}
    active_count = 0
    with DOWNLOAD_JOBS_LOCK:
        now = time.time()
        expired = [key for key, job in DOWNLOAD_JOBS.items() if job.get("status") not in {"queued", "running"} and now - job.get("created", now) > 3600]
        for key in expired:
            old_job = DOWNLOAD_JOBS.pop(key, {})
            if old_job.get("temp_dir"):
                shutil.rmtree(old_job["temp_dir"], ignore_errors=True)
        for job in DOWNLOAD_JOBS.values():
            if job.get("status") in {"queued", "running"}:
                active_count += 1
        if active_count >= 5:
            return jsonify({"ok": False, "error": "Antrean sedang penuh. Tunggu unduhan sebelumnya selesai dulu."}), 429
        job_id = secrets.token_urlsafe(18)
        DOWNLOAD_JOBS[job_id] = {
            "id": job_id, "status": "queued", "percent": 0, "text": "Menyiapkan unduhan...",
            "created": time.time(), "updated": time.time(), "data": data, "note": "",
        }
    thread = threading.Thread(target=_download_job_worker, args=(job_id, data), daemon=True)
    thread.start()
    return jsonify({"ok": True, "job_id": job_id}), 202


def _download_job_worker(job_id: str, data: dict):
    _update_job(job_id, status="running", percent=1, text="Memulai proses...", stage="starting")
    try:
        result_path, temp_dir, mimetype = _process_download(data, job_id)
        _update_job(job_id, status="complete", percent=100, text="Selesai! File siap diunduh.", stage="complete",
                    result_path=str(result_path), temp_dir=str(temp_dir), mimetype=mimetype,
                    filename=result_path.name)
    except Exception as exc:
        # Keep the full traceback in hosting logs; the browser receives a safe,
        # concise message that still identifies otherwise-empty exceptions.
        logger.exception("Pekerjaan unduh KonversiKu gagal (job_id=%s)", job_id)
        raw = exception_detail(exc, 500)
        _update_job(job_id, status="error", text="Pengunduhan gagal.", error=raw, stage="error")


@app.get("/api/download/jobs/<job_id>")
def download_job_status(job_id):
    with DOWNLOAD_JOBS_LOCK:
        job = DOWNLOAD_JOBS.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "ID unduhan tidak ditemukan atau sudah kedaluwarsa."}), 404
        safe = {key: job.get(key) for key in ("id", "status", "percent", "text", "stage", "error", "note", "filename")}
    safe["ok"] = True
    return jsonify(safe)


@app.get("/api/download/jobs/<job_id>/file")
def download_job_file(job_id):
    with DOWNLOAD_JOBS_LOCK:
        job = DOWNLOAD_JOBS.get(job_id)
        if not job or job.get("status") != "complete" or not job.get("result_path"):
            return jsonify({"ok": False, "error": "File belum siap atau unduhan sudah kedaluwarsa."}), 404
        if job.get("downloaded"):
            return jsonify({"ok": False, "error": "File ini sudah diambil. Mulai unduhan baru untuk mengulang."}), 410
        job["downloaded"] = True
        result_path = Path(job["result_path"])
        temp_dir = job["temp_dir"]
        mimetype = job.get("mimetype")
    if not result_path.exists():
        return jsonify({"ok": False, "error": "File hasil tidak ditemukan di server."}), 404
    response = send_file(result_path, as_attachment=True, download_name=result_path.name, mimetype=mimetype, conditional=True)
    def cleanup():
        shutil.rmtree(temp_dir, ignore_errors=True)
        _update_job(job_id, result_path=None, temp_dir=None, text="File sudah dikirim ke browser.", stage="downloaded")
    response.call_on_close(cleanup)
    return response


def _process_download(data: dict, job_id: str):
    url, platform = validated_media_url(data.get("url", ""))
    action = str(data.get("action", "media")).lower()
    output_format = str(data.get("format", "mp4")).lower()
    quality = str(data.get("quality", "best")).lower()
    photo_slideshow = platform == "TikTok" and is_tiktok_photo_url(url)
    temp_dir = Path(tempfile.mkdtemp(prefix="konversiku-download-"))
    try:
        if action in {"photo_video", "photo_zip", "photo_image"}:
            if not photo_slideshow:
                raise ValueError("Fitur foto-slide hanya berlaku untuk tautan TikTok /photo/.")
            details = resolve_tiktok_photo_post(url)
            urls = details["images"]
            if action == "photo_image":
                try:
                    index = int(data.get("image_index", -1))
                except (TypeError, ValueError):
                    index = -1
                if index < 0 or index >= len(urls):
                    raise ValueError("Nomor foto tidak valid atau tidak tersedia.")
                paths = _download_photo_set(url, temp_dir, job_id, indices=[index], details=details)
                path = paths[0]
                result_name = f"foto-tiktok-{index + 1:02d}{path.suffix.lower()}"
                result = temp_dir / result_name
                if result != path:
                    path.rename(result)
                _update_job(job_id, percent=95, text="Menyiapkan foto untuk diunduh...", stage="processing")
                return result, temp_dir, "application/octet-stream"
            image_paths = _download_photo_set(
                url, temp_dir, job_id, details=details,
                progress_end=45 if action == "photo_video" else 70,
            )
            if action == "photo_zip":
                _update_job(job_id, percent=85, text="Membuat ZIP berisi foto-foto...", stage="archiving")
                result = temp_dir / "tiktok-foto-slide.zip"
                with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for i, path in enumerate(image_paths, start=1):
                        archive.write(path, arcname=f"foto-{i:02d}{path.suffix.lower()}")
                return result, temp_dir, "application/zip"
            # Convert each slide to a vertical MP4, optionally with the attached soundtrack.
            audio_path, audio_duration = _download_photo_audio(url, temp_dir, job_id)
            result = temp_dir / "tiktok-foto-slide.mp4"
            _build_slideshow_video(image_paths, result, audio_path, audio_duration, job_id)
            _update_job(job_id, percent=98, text="Video selesai dibuat.", stage="finishing")
            return result, temp_dir, "video/mp4"

        if output_format not in {"mp4", "mp3"}:
            raise ValueError("Format dari tautan hanya mendukung MP4 atau MP3.")
        height_map = {"best": None, "1080": 1080, "720": 720, "480": 480}
        if quality not in height_map:
            raise ValueError("Pilihan kualitas tidak valid.")
        if output_format == "mp3" and quality != "best":
            quality = "best"
        if photo_slideshow and output_format != "mp3":
            raise ValueError("Untuk foto-slide TikTok pilih 'MP4 · Video dari foto slide' atau 'ZIP · Semua foto'.")
        download_url = tiktok_photo_audio_url(url) if photo_slideshow else url
        outtmpl = str(temp_dir / "%(title).90B-%(id)s.%(ext)s")
        opts = make_ydl_options(
            outtmpl=outtmpl, windowsfilenames=True, trim_file_name=120,
            progress_hooks=[_job_progress_hook(job_id, 5, 75, "Mengunduh media")],
        )
        manual_mp3_extract = False
        if output_format == "mp3":
            if photo_slideshow or platform == "SoundCloud":
                opts["format"] = "bestaudio/best"
                opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
            elif platform == "TikTok":
                opts["format"] = "best[vcodec!=none][acodec!=none]/bestvideo*[acodec!=none]"
                manual_mp3_extract = True
            elif platform == "Facebook":
                opts["format"] = "bestvideo*+bestaudio/best"
                opts["merge_output_format"] = "mkv"
                manual_mp3_extract = True
            else:
                opts["format"] = "best[vcodec!=none][acodec!=none]/bestvideo*+bestaudio/best"
                opts["merge_output_format"] = "mkv"
                manual_mp3_extract = True
        else:
            height = height_map[quality]
            opts["format"] = f"bestvideo*[height<={height}]+bestaudio/best[height<={height}]/best" if height else "bestvideo*+bestaudio/best"
            opts["merge_output_format"] = "mp4"

        with JOB_LIMIT:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([download_url])

        if output_format == "mp3" and manual_mp3_extract:
            _update_job(job_id, percent=84, text="Mengekstrak suara dari video...", stage="processing")
            video_exts = {".mp4", ".mkv", ".webm", ".mov", ".flv", ".avi", ".m4v", ".3gp"}
            sources = [p for p in temp_dir.iterdir() if p.is_file() and p.suffix.lower() in video_exts]
            if not sources:
                raise RuntimeError("Sumber tidak menyediakan video dengan trek audio yang bisa diekstrak.")
            source = max(sources, key=lambda p: p.stat().st_size)
            result = source.with_name(clean_download_name(source.stem, "media") + ".mp3")
            command = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-map", "0:a:0", "-vn", "-codec:a", "libmp3lame", "-b:a", "192k", str(result)]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=60 * 30)
            if completed.returncode != 0 or not result.exists() or result.stat().st_size == 0:
                lines = (completed.stderr or "File video tidak memiliki trek audio.").strip().splitlines()
                raise RuntimeError(lines[-1][:350] if lines else "Gagal mengekstrak audio.")
            return result, temp_dir, "audio/mpeg"

        expected_ext = ".mp3" if output_format == "mp3" else ".mp4"
        candidates = [p for p in temp_dir.iterdir() if p.is_file() and p.suffix.lower() == expected_ext]
        if not candidates:
            candidates = [p for p in temp_dir.iterdir() if p.is_file() and p.suffix.lower() in {".mp4", ".m4a", ".webm", ".mp3", ".mkv"}]
        if not candidates:
            raise RuntimeError("File hasil tidak ditemukan. Coba kualitas lain.")
        result = max(candidates, key=lambda p: p.stat().st_size)
        desired_ext = expected_ext if output_format == "mp3" else result.suffix.lower()
        final_path = result.with_name(clean_download_name(result.stem, "media") + desired_ext)
        if final_path != result:
            result.rename(final_path)
            result = final_path
        return result, temp_dir, "audio/mpeg" if output_format == "mp3" else "video/mp4"
    except ValueError:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        detail = exception_detail(exc, 500)
        if output_format == "mp3" and photo_slideshow:
            detail = "Lagu foto-slide tidak dapat diambil dari tautan ini atau akses TikTok dibatasi. " + detail
        raise RuntimeError("Gagal mengunduh atau mengonversi konten. " + detail) from exc


@app.post("/api/convert-file")
def convert_file():
    uploaded = request.files.get("file")
    output_format = str(request.form.get("format", "mp3")).lower()
    if uploaded is None or not uploaded.filename:
        return jsonify({"ok": False, "error": "Pilih file yang ingin dikonversi."}), 400
    if output_format not in FORMAT_EXTENSIONS:
        return jsonify({"ok": False, "error": "Format keluaran tidak didukung."}), 400

    temp_dir = tempfile.mkdtemp(prefix="konversiku-file-")
    original_name = secure_filename(uploaded.filename) or "media.bin"
    input_path = Path(temp_dir) / original_name
    output_stem = clean_download_name(Path(original_name).stem, "media")
    output_path = Path(temp_dir) / (output_stem + "-converted" + FORMAT_EXTENSIONS[output_format])
    uploaded.save(input_path)

    # The fixed argument lists avoid shell invocation and user-controlled FFmpeg flags.
    try:
        executable = ffmpeg_path()
        common = [executable, "-hide_banner", "-loglevel", "error", "-y", "-i", str(input_path)]
        if output_format == "mp3":
            command = common + ["-vn", "-codec:a", "libmp3lame", "-b:a", "192k", str(output_path)]
        elif output_format == "wav":
            command = common + ["-vn", "-codec:a", "pcm_s16le", str(output_path)]
        elif output_format == "m4a":
            command = common + ["-vn", "-codec:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output_path)]
        elif output_format == "mp4":
            command = common + ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output_path)]
        else:  # webm
            command = common + ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-c:a", "libopus", str(output_path)]

        import subprocess
        with JOB_LIMIT:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=60 * 60)
        if completed.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            reason = (completed.stderr or "Format sumber tidak cocok dengan format keluaran.").strip().splitlines()
            raise RuntimeError(reason[-1][:350] if reason else "FFmpeg tidak dapat memproses file ini.")
        mime_map = {
            "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4",
            "mp4": "video/mp4", "webm": "video/webm",
        }
        return send_temp_file(output_path, temp_dir, mime_map[output_format])
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.exception("Konversi file KonversiKu gagal")
        detail = exception_detail(exc, 350)
        return jsonify({"ok": False, "error": "File gagal dikonversi. Pastikan file valid dan format keluaran cocok. " + detail}), 400


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"ok": False, "error": "Ukuran file melewati batas 100 MB untuk layanan online ini. Pilih file lebih kecil."}), 413


def get_lan_ipv4_addresses() -> list[str]:
    """Return likely LAN IPv4 addresses for opening the app from another device."""
    found: list[str] = []
    # Ask the operating system which local interface it would use for outbound traffic.
    # UDP connect selects a route but does not send application data.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))  # TEST-NET address; no payload is sent
            addr = probe.getsockname()[0]
            if addr and not addr.startswith(("127.", "169.254.")):
                found.append(addr)
    except OSError:
        pass

    # Also include non-loopback IPv4 addresses discovered on host interfaces.
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = result[4][0]
            if addr and not addr.startswith(("127.", "169.254.")) and addr not in found:
                found.append(addr)
    except OSError:
        pass
    return found


if __name__ == "__main__":
    print(f"\nKonversiKu v{APP_VERSION} berjalan di:")
    print("  Laptop ini : http://127.0.0.1:5000")
    addresses = get_lan_ipv4_addresses()
    if addresses:
        print("  Coba dari HP (HP dan laptop harus di Wi-Fi yang sama):")
        for addr in addresses:
            print(f"    http://{addr}:5000")
    else:
        print("  IP jaringan tidak terdeteksi. Jalankan ipconfig untuk melihat IPv4 Wi-Fi.")
    print("\nJangan tutup jendela server saat website sedang digunakan.")
    print("Gunakan hanya di jaringan pribadi tepercaya; jangan buka port ini ke internet.")
    print("Tekan Ctrl+C untuk menghentikan server.\n")
    # Bind on LAN interfaces so a phone/tablet on the same trusted Wi-Fi can connect.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False, threaded=True)
