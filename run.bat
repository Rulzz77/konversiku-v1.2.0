@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo =====================================
echo          KonversiKu - Web App v1.2.0
echo =====================================
echo.
where py >nul 2>nul
if %errorlevel%==0 (
  set "PYTHON_CMD=py -3"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python belum ditemukan. Install Python 3.11+ dari https://www.python.org/downloads/
    echo Saat instalasi, centang Add Python to PATH.
    pause
    exit /b 1
  )
  set "PYTHON_CMD=python"
)
%PYTHON_CMD% --version
if errorlevel 1 (
  echo Python tidak bisa dijalankan.
  pause
  exit /b 1
)
echo.
echo Menyiapkan paket yang dibutuhkan...
%PYTHON_CMD% -m pip install --upgrade pip
if errorlevel 1 (
  echo Gagal memperbarui pip. Periksa koneksi internet, lalu coba lagi.
  pause
  exit /b 1
)
%PYTHON_CMD% -m pip install --upgrade -r requirements.txt
if errorlevel 1 (
  echo Instalasi paket gagal. Periksa koneksi internet dan versi Python.
  pause
  exit /b 1
)
echo.
echo Menjalankan server lokal dan jaringan Wi-Fi...
start "KonversiKu Server" /min cmd /k "%PYTHON_CMD% app.py"
echo Menunggu server siap...
for /L %%i in (1,1,30) do (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:5000/api/health' -TimeoutSec 1; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>nul
  if not errorlevel 1 goto :server_ready
  timeout /t 1 /nobreak >nul
)
echo.
echo Server belum siap atau berhenti karena error.
echo Periksa jendela KonversiKu Server untuk pesan error.
pause
exit /b 1

:server_ready
echo Server siap!
echo.
echo Buka di laptop: http://127.0.0.1:5000
echo.
for /f "delims=" %%I in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = Get-NetIPConfiguration ^| Where-Object { $_.IPv4DefaultGateway -ne $null -and $_.NetAdapter.Status -eq 'Up' } ^| Select-Object -First 1 -ExpandProperty IPv4Address; if ($c) { $c.IPAddress }" 2^>nul') do set "PHONE_IP=%%I"
if defined PHONE_IP (
  echo Buka di HP ^(pastikan HP dan laptop memakai Wi-Fi yang sama^):
  echo http://%PHONE_IP%:5000
) else (
  echo Untuk alamat HP, lihat IPv4 Address pada adapter Wi-Fi dari perintah ipconfig.
  echo Lalu buka http://ALAMAT-IP-LAPTOP:5000 di browser HP.
)
echo.
echo Jika Windows Firewall bertanya, izinkan Python hanya di jaringan Private.
echo Jangan gunakan di Wi-Fi publik dan jangan buka port ini ke internet.
echo.
start "" "http://127.0.0.1:5000"
echo Browser laptop dibuka. Biarkan jendela KonversiKu Server tetap berjalan.
echo Jika HP tidak bisa terhubung, lihat README.md bagian Akses dari HP.
pause
