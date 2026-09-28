@echo off
REM =============================================================================
REM ClipperAI — STOP SEMUA (Windows)
REM
REM Menghentikan API dan WEB milik repo ini (proses lain tidak disentuh).
REM
REM Memakai PowerShell (bukan rantai `taskkill` + `wmic`) karena:
REM   * wmic sudah deprecated dan bisa hilang di Windows baru;
REM   * `taskkill /FI "WINDOWTITLE eq ..."` tidak andal untuk jendela `cmd /k`
REM     yang judulnya berubah setelah perintah berjalan;
REM   * pencocokan lewat command-line + port menangkap seluruh pohon proses,
REM     termasuk anak yang di-spawn (next dev).
REM
REM Dijalankan lewat file .ps1 terpisah agar logika tidak perlu di-escape
REM berlapis-lapis lewat cmd ^.
REM =============================================================================
setlocal EnableExtensions

echo.
echo  Menghentikan layanan ClipperAI...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-all.ps1"
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo  Selesai. Port 8000 dan 3000 bebas.
) else (
  echo  [!] Ada port yang belum bebas. Cek: netstat -ano -p tcp ^| findstr ":8000 :3000"
)
echo.
exit /b %RC%
