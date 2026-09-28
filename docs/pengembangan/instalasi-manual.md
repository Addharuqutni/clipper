# Instalasi manual

`npm run dev` dan `start.cmd` menjalankan `scripts\setup.cmd`, yang
mengerjakan semua langkah di halaman ini dan melewati yang sudah beres. Ikuti
halaman ini hanya bila ingin memasang sendiri atau setup otomatis gagal.

## FFmpeg

Wajib punya `libass` (subtitle karaoke) dan `libx264`. `setup.cmd` mengunduh
build [BtbN](https://github.com/BtbN/FFmpeg-Builds/releases)
(`ffmpeg-master-latest-win64-gpl`); build
[gyan.dev](https://www.gyan.dev/ffmpeg/builds/) juga memenuhi. Taruh di:

```
.libs\ffmpeg\ffmpeg.exe
.libs\ffmpeg\ffprobe.exe
```

Verifikasi di PowerShell:

```powershell
.\.libs\ffmpeg\ffmpeg.exe -hide_banner -version | Select-String libass
.\.libs\ffmpeg\ffmpeg.exe -hide_banner -encoders | Select-String libx264
```

Build BtbN memakai TLS Schannel. Aman untuk alur sekarang (yt-dlp mengunduh
sendiri; FFmpeg hanya menggabungkan berkas lokal). Bila kelak FFmpeg dipakai
mengunduh rentang HTTPS (`--download-sections`), ganti ke build gyan.dev.

## Python

```powershell
py -3 -m venv .venv-win
.\.venv-win\Scripts\python.exe -m pip install -e ".[app]"
```

Satu perintah memasang API dan pipeline (yang berjalan di dalam proses API),
termasuk `yt-dlp[default]` beserta `yt-dlp-ejs` untuk tantangan JavaScript
YouTube. Python 3.12–3.14 didukung (`mediapipe`, `ctranslate2`, dan `opencv`
punya wheel `cp314`).

## Model

- `face_landmarker.task` (~4 MB) ke `.models\` — diunduh `setup.cmd` dari
  `storage.googleapis.com/mediapipe-models/...`. Worker **tidak** mengunduhnya
  sendiri.
- Whisper `small` (~460 MB) diunduh otomatis saat transkripsi pertama.

## Frontend

Dari **root repo** (bukan `apps\web`):

```powershell
npm ci
```

## `.env`

Buat `.env` berisi minimal `TOKEN_ENCRYPTION_KEY` (32 byte acak, base64):

```powershell
.\.venv-win\Scripts\python.exe -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"
```

Lihat [Konfigurasi](konfigurasi.md) untuk variabel lainnya.

## Menjalankan tanpa launcher

Launcher (`npm run dev`) menyiapkan PYTHONPATH. Bila ingin menjalankan
sendiri, butuh dua terminal:

```powershell
# Terminal 1 — API (pipeline ikut berjalan di dalamnya)
$env:PYTHONPATH = "$PWD\apps\api;$PWD\packages\shared\src;$PWD\apps\worker-light;$PWD\apps\worker-render"
cd apps\api
..\..\.venv-win\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```powershell
# Terminal 2 — frontend
npm run dev:web
```

`.env` dan jalur bawaan tetap berlaku (dimuat oleh API sendiri).
