@echo off
REM =============================================================================
REM ClipperAI — SETUP otomatis (Windows)
REM
REM Menyiapkan semua yang dibutuhkan start.cmd:
REM   1. .env              (dibuat berisi TOKEN_ENCRYPTION_KEY acak)
REM   2. .venv-win         (virtualenv Python 3.12-3.14 + pip install)
REM   3. FFmpeg            (diunduh ke .libs\ffmpeg\ bila belum ada)
REM   4. face_landmarker   (model MediaPipe, diunduh ke .models\)
REM   5. node_modules      (npm ci di root repo)
REM
REM Idempoten: langkah yang sudah beres dilewati. Dependensi dipasang ulang
REM otomatis bila pyproject.toml / package-lock.json berubah sejak pemasangan
REM terakhir. Tidak menimpa .env dan tidak menghapus data.
REM =============================================================================
setlocal EnableExtensions
call "%~dp0_env.cmd"

call :ensure_env || exit /b 1
call :ensure_python || exit /b 1
call :ensure_ffmpeg || exit /b 1
call :ensure_face_model || exit /b 1
call :ensure_node || exit /b 1
echo   [ok] setup selesai
exit /b 0

REM -----------------------------------------------------------------------------
:ensure_env
if exist "%ROOT%\.env" (
  echo   [ok] .env
  exit /b 0
)
set "KEY="
for /f "delims=" %%K in ('powershell -NoProfile -Command "$b=New-Object byte[] 32;[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b);[Convert]::ToBase64String($b)"') do set "KEY=%%K"
if not defined KEY (
  echo   [X] Gagal membuat TOKEN_ENCRYPTION_KEY lewat PowerShell.
  exit /b 1
)
> "%ROOT%\.env" echo TOKEN_ENCRYPTION_KEY=%KEY%
echo   [ok] .env dibuat ^(kunci enkripsi acak^)
exit /b 0

REM -----------------------------------------------------------------------------
:ensure_python
if exist "%PY%" goto :python_deps
REM Pilih interpreter yang benar-benar jalan dan versinya didukung. `where python`
REM saja tidak cukup: alias Microsoft Store ada di PATH tetapi hanya membuka Store.
set "SYS_PY="
for %%V in (3.14 3.13 3.12) do (
  if not defined SYS_PY py -%%V -c "import sys" >nul 2>&1 && set "SYS_PY=py -%%V"
)
if not defined SYS_PY (
  python -c "import sys; sys.exit(not (3,12) <= sys.version_info[:2] < (3,15))" >nul 2>&1 && set "SYS_PY=python"
)
if not defined SYS_PY (
  echo   [X] Python 3.12-3.14 tidak ditemukan. Pasang dari https://www.python.org lalu ulangi.
  exit /b 1
)
echo   ... membuat virtualenv .venv-win ^(%SYS_PY%^)
%SYS_PY% -m venv "%VENV%" || exit /b 1

:python_deps
REM Salinan pyproject.toml saat pemasangan terakhir; beda isi = pasang ulang.
set "STAMP=%VENV%\.clipper-pyproject.toml"
if exist "%STAMP%" fc /b "%ROOT%\pyproject.toml" "%STAMP%" >nul 2>&1 && (
  echo   [ok] dependensi Python
  exit /b 0
)
echo   ... pip install (pertama kali bisa 5-10 menit)
pushd "%ROOT%"
"%PY%" -m pip install --upgrade pip >nul
"%PY%" -m pip install -e ".[app]"
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" (
  echo   [X] pip install gagal. Lihat pesan di atas.
  exit /b 1
)
copy /y "%ROOT%\pyproject.toml" "%STAMP%" >nul
echo   [ok] dependensi Python terpasang
exit /b 0

REM -----------------------------------------------------------------------------
:ensure_ffmpeg
set "FF=%ROOT%\.libs\ffmpeg\ffmpeg.exe"
if defined FFMPEG_BINARY set "FF=%FFMPEG_BINARY%"
if exist "%FF%" (
  echo   [ok] FFmpeg
  exit /b 0
)
REM Build "latest" BtbN (libass + libx264). Tidak dipin ke tag tertentu karena
REM BtbN menghapus rilis bertanggal lama; tag tetap akan 404 beberapa bulan lagi.
echo   ... mengunduh FFmpeg build BtbN (libass + libx264, ~200 MB)
set "FF_TMP=%TEMP%\clipper-ffmpeg"
if exist "%FF_TMP%" rmdir /s /q "%FF_TMP%"
mkdir "%FF_TMP%"
curl -L --fail --progress-bar -o "%FF_TMP%\ffmpeg.zip" https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip || (
  echo   [X] Unduhan FFmpeg gagal. Taruh manual ffmpeg.exe + ffprobe.exe di .libs\ffmpeg\
  exit /b 1
)
tar -xf "%FF_TMP%\ffmpeg.zip" -C "%FF_TMP%" || exit /b 1
mkdir "%ROOT%\.libs\ffmpeg" 2>nul
for /d %%D in ("%FF_TMP%\ffmpeg-*") do copy /y "%%D\bin\*.exe" "%ROOT%\.libs\ffmpeg\" >nul
rmdir /s /q "%FF_TMP%"
if not exist "%ROOT%\.libs\ffmpeg\ffmpeg.exe" (
  echo   [X] ffmpeg.exe tidak ada di arsip yang diunduh.
  exit /b 1
)
"%ROOT%\.libs\ffmpeg\ffmpeg.exe" -hide_banner -filters 2>nul | findstr /r /c:" ass " >nul || (
  echo   [X] FFmpeg terunduh tanpa filter libass; subtitle tidak dapat dibakar.
  exit /b 1
)
echo   [ok] FFmpeg terpasang di .libs\ffmpeg\
exit /b 0

REM -----------------------------------------------------------------------------
:ensure_face_model
set "FACE=%ROOT%\.models\face_landmarker.task"
if defined FACE_LANDMARKER_MODEL set "FACE=%FACE_LANDMARKER_MODEL%"
if exist "%FACE%" (
  echo   [ok] model face_landmarker
  exit /b 0
)
echo   ... mengunduh model face_landmarker (~4 MB)
REM Unduh ke .part lalu rename: unduhan yang terputus tidak meninggalkan berkas
REM setengah jadi yang dianggap sudah terpasang pada run berikutnya.
curl -L --fail --create-dirs -s -o "%FACE%.part" https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task || (
  del "%FACE%.part" 2>nul
  echo   [X] Unduhan model face_landmarker gagal.
  exit /b 1
)
move /y "%FACE%.part" "%FACE%" >nul
echo   [ok] model face_landmarker terpasang
exit /b 0

REM -----------------------------------------------------------------------------
:ensure_node
where npm >nul 2>&1 || (
  echo   [X] Node.js 22+ tidak ditemukan. Pasang dari https://nodejs.org lalu ulangi.
  exit /b 1
)
set "LOCK_STAMP=%ROOT%\node_modules\.clipper-package-lock.json"
if exist "%LOCK_STAMP%" fc /b "%ROOT%\package-lock.json" "%LOCK_STAMP%" >nul 2>&1 && (
  echo   [ok] node_modules
  exit /b 0
)
echo   ... npm ci
pushd "%ROOT%"
call npm ci
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" (
  echo   [X] npm ci gagal. Lihat pesan di atas.
  exit /b 1
)
copy /y "%ROOT%\package-lock.json" "%LOCK_STAMP%" >nul
echo   [ok] node_modules terpasang
exit /b 0
