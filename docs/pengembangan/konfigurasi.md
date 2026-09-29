# Konfigurasi

Konfigurasi **tidak perlu disentuh**: `start.cmd` membuat `.env` berisi
`TOKEN_ENCRYPTION_KEY` acak, dan semua nilai lain punya bawaan.

`.env` di akar repo dimuat API lewat python-dotenv (`apps/api/app/core/config.py`)
ke environment proses, sehingga API dan worker (thread di proses yang sama)
membaca nilai yang sama. Variabel environment yang sudah diset menang atas
`.env`.

## `TOKEN_ENCRYPTION_KEY`

Mengenkripsi kunci API penyedia AI dan cookies YouTube. **Jangan diganti**
tanpa alasan: data terenkripsi jadi tidak bisa dibaca dan harus diisi ulang di
`/settings` dan halaman YouTube. Simpan cadangannya bila memindahkan data.

## Lokasi berkas

Jalur relatif ditafsirkan terhadap folder repo (bukan direktori kerja), jadi
repo bisa dipindah tanpa mengedit `.env`.

| Variabel | Bawaan |
|---|---|
| `LOCAL_STORAGE_DIR` | `output` (SQLite + semua media) |
| `CLIPS_OUTPUT_DIR` | `output/clips` |
| `WORKER_WORKSPACE_DIR` | `.work` |
| `WHISPER_MODEL_CACHE_DIR` | `.models` |
| `FACE_LANDMARKER_MODEL` | `.models/face_landmarker.task` |
| `FFMPEG_BINARY`, `FFPROBE_BINARY` | `.libs/ffmpeg/*.exe` bila ada, lalu `PATH` |

## Variabel yang mungkin ingin diubah

| Variabel | Bawaan | Fungsi |
|---|---|---|
| `MAX_VIDEO_DURATION_MIN` | `180` | Batas atas durasi video (menit). Berlaku = min(nilai ini, kapasitas konteks model AI). |
| `AI_CONTEXT_TOKENS` | kosong | Konteks model bila penyedia AI diatur lewat env. Kosong = 128.000. |
| `WHISPER_MODEL` | `large-v3-turbo` | Model transkripsi. Paling akurat untuk bahasa Indonesia (±15% kata salah vs ±22% `small`), ±1× durasi video di CPU 4 core. `small` ±3× lebih cepat. |
| `WHISPER_CPU_THREADS` | core fisik | Thread Whisper. Hyperthread justru memperlambat. |
| `STT_BACKEND` | `local` | `remote` = API OpenAI-compatible (`WHISPER_API_KEY`, `WHISPER_API_BASE_URL`). |
| `AUTO_RENDER_FINAL` | `true` | Render 1080p semua klip otomatis setelah analisis. |
| `RENDER_SLOTS`, `STT_SLOTS` | `1` | Render/transkripsi paralel. Naikkan hanya bila CPU ≥ 8 core. |
| `INGEST_WORKERS` | `2` | Unduhan/analisis paralel. |
| `FFMPEG_THREADS` | `2` | Thread FFmpeg (decoder dan encoder) per render. |
| `RAW_MEDIA_TTL_HOURS` | `48` | Video sumber dihapus N jam setelah render terakhir job selesai. |
| `YTDLP_TIMEOUT_S` | `1800` | Batas waktu unduhan YouTube. |

Penyedia AI biasanya diatur di `/settings` (disimpan di basis data). Cadangan
lewat env dipakai hanya bila belum ada pengaturan tersimpan:
`AI_PROVIDER_PRESET`, `CUSTOM_AI_BASE_URL`, `CUSTOM_AI_MODEL`,
`CUSTOM_AI_API_KEY`, `GEMINI_API_KEY`, `ALLOW_PRIVATE_AI_HOST`.

Daftar lengkap ada di [`.env.example`](../../.env.example).
