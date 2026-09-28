@echo off
REM =============================================================================
REM ClipperAI — jalankan BACKEND (FastAPI + pipeline) di Windows.
REM
REM Prasyarat: start.cmd / scripts\setup.cmd sudah dijalankan sekali.
REM Endpoint: http://localhost:8000   (dokumentasi: /docs)
REM
REM Hanya 127.0.0.1: API tidak punya login, jadi tidak boleh terjangkau dari
REM jaringan (Wi-Fi yang sama bisa mengambil kunci API dan cookies).
REM Tanpa --reload: pipeline berjalan di proses ini, dan reload membunuh job
REM yang sedang diproses setiap kali berkas berubah.
REM =============================================================================
setlocal EnableExtensions
call "%~dp0_env.cmd"

if not exist "%PY%" (
  echo [X] Python venv tidak ditemukan: "%PY%"
  echo     Jalankan dulu:  start.cmd
  exit /b 1
)

cd /d "%ROOT%\apps\api" || exit /b 1
set "PYTHONPATH=%ROOT%\apps\api;%ROOT%\packages\shared\src;%ROOT%\apps\worker-light;%ROOT%\apps\worker-render"

echo.
echo  [API] http://localhost:8000   ^(docs: /docs^)
echo.
"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
exit /b %ERRORLEVEL%
