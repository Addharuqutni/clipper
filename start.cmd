@echo off
REM =============================================================================
REM ClipperAI — klik dua kali untuk menjalankan.
REM
REM Satu jendela saja: setup otomatis (pemakaian pertama), lalu API + web
REM dengan log gabungan. Browser terbuka
REM sendiri di http://localhost:3000.
REM Hentikan: Ctrl+C atau tutup jendela (stop.cmd bila ada yang tertinggal).
REM
REM Jendela dijalankan langsung oleh node, bukan oleh skrip .cmd ini: tanpa
REM batch di antaranya, Ctrl+C tidak memunculkan "Terminate batch job (Y/N)?".
REM =============================================================================
where node >nul 2>&1 || (
  echo   [X] Node.js 22+ tidak ditemukan. Pasang dari https://nodejs.org lalu ulangi.
  pause
  exit /b 1
)
start "ClipperAI" node "%~dp0scripts\dev.mjs" --open
