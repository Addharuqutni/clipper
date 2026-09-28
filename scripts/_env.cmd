@echo off
REM =============================================================================
REM ClipperAI — helper: set ROOT, VENV, PY untuk launcher lain.
REM
REM Pemakaian:   call "%~dp0_env.cmd"
REM
REM .env TIDAK diurai di sini: API memuatnya sendiri lewat python-dotenv
REM (apps\api\app\core\config.py), yang memahami kutip, spasi, dan komentar.
REM Jalur bawaan (output\, .work\, .models\, .libs\ffmpeg\) ditentukan di
REM clipper_shared.storage, relatif terhadap folder repo.
REM =============================================================================
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
if not defined VENV set "VENV=%ROOT%\.venv-win"
if not defined PY set "PY=%VENV%\Scripts\python.exe"
