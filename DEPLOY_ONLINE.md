# KonversiKu v1.2.0 — Online-ready

Versi ini disiapkan agar bisa dipasang satu kali ke hosting, lalu dibuka dari HP/laptop lewat alamat web tanpa menjalankan `run.bat` di perangkat sendiri.

## Yang berubah di v1.2.0

- **Progres unduhan:** menampilkan persen aktual dari yt-dlp, status pemrosesan, kecepatan dan ETA jika sumber melaporkannya.
- **TikTok foto-slide (`/photo/ID`):** mencoba membaca daftar foto publik, mengunduh foto per foto atau seluruhnya sebagai ZIP, dan menyusun foto menjadi MP4 vertikal dengan lagu postingan jika tersedia. MP3 soundtrack juga masih tersedia sebagai pilihan.

- **Tidak perlu server lokal di laptop setelah online.** Backend berjalan di hosting; browser HP cukup membuka URL hosting.
- **Akses dilindungi kode.** Hosting meminta `KONVERSIKU_ACCESS_CODE` agar endpoint unduhan tidak menjadi layanan publik terbuka.
- **Facebook MP3:** mencoba menggabungkan stream video dan audio dahulu sebelum mengambil audio.
- **TikTok MP3:** mengutamakan format video yang sudah memiliki audio dan tidak fallback sengaja ke halaman/track audio musik terpisah. Bila platform tidak menyertakan suara asli dalam format yang dapat diunduh, aplikasi akan memberi error alih-alih mengklaim bisa memulihkan suara yang tidak ada.
- Antarmuka responsif, MP4/MP3 dari tautan, dan konversi file yang diunggah (batas default 100 MB).

## Cara pasang online (sekali saja)

Aplikasi ini **belum memiliki URL publik** sebelum kamu menghubungkan source code ke akun hosting milikmu. ZIP tidak bisa langsung menjadi website publik hanya dengan dibuka.

### 1. Upload ke GitHub

1. Download dan ekstrak ZIP ini.
2. Buat repository baru di GitHub, misalnya `konversiku`.
3. Upload semua isi folder hasil ekstrak versi `v1.2.0` ke root repository (termasuk `app.py`, `templates`, `requirements.txt`, dan `render.yaml`).
4. Pastikan repository berisi file `render.yaml` di root.

### 2. Buat web service di Render

1. Masuk ke https://render.com/ dan hubungkan akun GitHub.
2. Pilih **New → Blueprint** (atau New → Web Service dan pilih repository).
3. Pilih repository `konversiku` yang baru dibuat. Render akan membaca `render.yaml`.
4. Saat diminta mengisi `KONVERSIKU_ACCESS_CODE`, buat kode rahasia yang tidak mudah ditebak. Jangan menuliskannya di repository.
5. Deploy dan tunggu sampai status layanan `Live`.
6. Buka alamat `https://...onrender.com` yang diberikan Render dari HP atau laptop. Masukkan kode akses pada halaman login.

### 3. Catatan hosting gratis

- Hosting gratis bisa tidur setelah tidak aktif; pembukaan pertama setelah tidur dapat lambat.
- CPU/memori/batas waktu hosting gratis dapat membatasi video besar. Mulai dengan konten pendek dan publik.
- File yang diunggah untuk konversi dikirim ke hosting. Jangan unggah dokumen pribadi atau media sensitif.
- Gunakan hanya konten yang kamu miliki atau berhak simpan. Tautan privat/login, konten berbayar/DRM, atau platform yang mengubah sistemnya bisa gagal.
- Tidak ada API key yang diperlukan. Namun, dukungan link berasal dari yt-dlp dan dapat berubah; lihat daftar dukungan terkini di https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md.

## Tentang suara TikTok / Facebook

Untuk MP3 dari video, KonversiKu berusaha mengekstrak audio dari media video yang tersedia (bukan sengaja mengambil halaman musik TikTok). Untuk tautan foto-slide TikTok `/photo/ID`, versi ini secara khusus mencoba mengambil soundtrack postingan sebagai MP3 menggunakan jalur `/video/ID`. Jika video yang disediakan platform hanya memiliki trek musik, suara asli tidak ada dalam file sumber, atau akses ditolak platform, software tidak bisa menciptakan kembali suara yang tidak tersedia. Bila hasil tetap hanya musik, uji unduhan MP4 dari post yang sama: jika MP4 itu juga hanya berisi musik, sumber unduhnya memang tidak menyertakan campuran suara yang kamu harapkan.

## Jalankan lokal (opsional)

Untuk pengujian di laptop, install Python 3.11+, lalu klik `run.bat`. Ini opsional; setelah deploy online, HP tidak perlu terhubung ke laptop kamu.
