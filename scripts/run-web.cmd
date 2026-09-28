@echo off
REM =============================================================================
REM ClipperAI — jalankan FRONTEND (Next.js dev server) di Windows.
REM
REM Endpoint: http://localhost:3000
REM =============================================================================
setlocal EnableExtensions
call "%~dp0_env.cmd" || exit /b 1

where npm >nul 2>&1 || (
  echo [X] npm tidak ditemukan di PATH. Pasang Node.js 22+.
  exit /b 1
)

REM npm workspaces meng-hoist dependensi ke node_modules di ROOT, bukan apps\web.
if not exist "%ROOT%\node_modules\next" (
  echo [!] node_modules belum ada — menjalankan "npm ci" di root dulu...
  pushd "%ROOT%" && call npm ci && popd
)

cd /d "%ROOT%\apps\web" || exit /b 1

echo.
echo  [WEB] http://localhost:3000
echo  [WEB] Ctrl+C untuk berhenti.
echo.
call npm run dev
exit /b %ERRORLEVEL%
