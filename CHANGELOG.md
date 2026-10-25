# Changelog

## v1.2.0
- Show actual download progress percentage, speed/ETA when reported by yt-dlp, and processing stage text via background jobs and polling.
- Add public TikTok photo-slide image resolver with URL allow-list, bounded image count, and file size limits.
- Add MP4 slideshow creation from photos, attempting to include the post soundtrack when available.
- Add ZIP download for all slideshow photos and individual photo download buttons.
- Add status polling and single-use file delivery endpoints for download jobs.

## v1.1.1
- Detect public TikTok photo-slide permalink patterns (`/@user/photo/ID`).
- Route metadata/audio requests through the corresponding `/video/ID` path as a best-effort workaround to download the attached soundtrack as MP3.
- Upgrade minimum yt-dlp dependency to 2026.10.21.
