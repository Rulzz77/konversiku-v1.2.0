# KonversiKu v1.2.0 — Progress download & TikTok photo-slide

Website converter untuk link media publik dan file milik pengguna. Versi ini menambahkan progres unduhan aktual serta proses foto-slide TikTok.

## Perubahan v1.2.0

- Progres unduhan berbasis data yt-dlp: persen, status proses, kecepatan dan perkiraan sisa waktu jika tersedia.
- Link TikTok `@akun/photo/ID` dapat dicoba dibaca sebagai daftar foto publik.
- Foto dapat diunduh satu per satu, semua foto dapat dikemas ke ZIP, dan foto dapat disusun menjadi MP4 vertikal.
- Saat membuat MP4, KonversiKu mencoba menambahkan lagu postingan; jika trek audio tidak bisa diambil, MP4 tetap dapat dibuat tanpa suara dan pesan hasil memberi tahu pengguna.
- Batas keamanan: maksimal 30 foto, 12 MB per foto, 80 MB total; hanya URL CDN HTTPS TikTok yang diizinkan.

Resolusi foto menggunakan metadata publik yang bisa berubah atau dibatasi platform. Jika postingan meminta login/akses khusus, daftar foto mungkin tidak bisa dibaca.

## Jalankan online

1. Ekstrak ZIP.
2. Upload semua isi folder ke repository GitHub.
3. Di Render, pilih **New → Blueprint**, lalu pilih repository dengan `render.yaml`.
4. Atur `KONVERSIKU_ACCESS_CODE` sebagai environment variable rahasia.
5. Setelah status `Live`, buka URL Render di HP atau laptop dan masukkan kode akses.

Sesudah deploy online, pengguna tidak perlu menjalankan `run.bat` pada laptop. Hosting tetap menjadi server pemroses, dan paket gratis mungkin memiliki batas CPU/memori/waktu eksekusi.

## Jalankan lokal (opsional)

Python 3.11+; jalankan `run.bat` di Windows atau `./run.sh` di Linux/macOS.

## Catatan

Gunakan hanya media yang kamu miliki atau berhak simpan. Konten privat, berbayar, login-gated, atau dibatasi platform dapat gagal. Tidak ada jaminan setiap link akan bisa diunduh. File unggahan dikirim ke hosting dan diproses sementara; jangan unggah file sensitif.
