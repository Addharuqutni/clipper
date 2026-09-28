# Arsitektur

Satu proses Python menjalankan API **dan** seluruh pipeline. Tidak ada
PostgreSQL, Redis, Celery, S3, WSL, atau Docker.

```
┌────────────────────────────────────────────────────────┐
│  Next.js (:3000)                                       │
└──────────────────────────┬─────────────────────────────┘
                           │ HTTP REST + SSE (127.0.0.1)
                           ▼
┌────────────────────────────────────────────────────────┐
│  FastAPI (:8000, hanya 127.0.0.1)                      │
│  · clipper_shared.dispatcher: thread pool per tahap    │
│      ingest (2)  ·  stt (STT_SLOTS=1)  ·  render (1)   │
│  · LocalEventBus: status job → SSE                     │
└──────────────┬──────────────────────────┬──────────────┘
               ▼                          ▼
   SQLite output/clipper.db       Disk output/ (raw, render, clips)
```

**Ukuran pool = batas paralel.** Satu transkripsi dan satu render sekaligus
secara bawaan; pekerjaan lain menunggu di antrean pool-nya. Pool terpisah
membuat antrean render yang panjang tidak menahan job baru di tahap ingest.

**Status job** ditulis ke SQLite oleh `clipper_shared.worker_events.emit`,
lalu disiarkan ke koneksi SSE di proses yang sama. `emit` melempar
`JobCanceled` bila job dibatalkan/dihapus. **Pembatalan juga memutus proses
anak**: setiap peluncuran FFmpeg/ffprobe/yt-dlp lewat `clipper_shared.processes`
sehingga terdaftar per job, dan endpoint cancel memanggil `terminate_job`
(pohon proses dimatikan dengan `taskkill /T /F` di Windows; POSIX memakai
`proc.kill`). Whisper lokal diperiksa antar segmen karena generator segmennya
malas. Job yang dibatalkan berakhir `canceled`, bukan `failed`.

**Saat startup** (`app/main.py`): skema SQLite dibuat/diselaraskan, job dan
render yang masih `queued`/`running` ditandai gagal (pasti sisa sesi yang
terputus), ruang kerja sementara disapu, dan pembersihan media kedaluwarsa
dijadwalkan tiap jam.

## Tahap pipeline

| Tahap | Kode | Pekerjaan |
|---|---|---|
| ingest | `worker_light.tasks.ingest_media` | YouTube: metadata, cek durasi, subtitle sesuai bahasa video, unduh (maks 1080p). Upload: berkas sudah ada di disk; cek durasi. |
| transcribe | `worker_light.tasks.transcribe_media` | faster-whisper (`small`, int8, CPU), atau API remote per potongan 10 menit. Dilewati bila subtitle YouTube tersedia. |
| analyze | `worker_light.tasks.score_segments` | AI memilih segmen 25–65 detik (4 percobaan untuk gangguan sementara). Gagal → segmen cadangan dari kepadatan bicara. |
| render | `worker_render.tasks.render_clip` | Reframe 9:16 + subtitle ASS dalam **satu** encode; overlay B-roll (bila ada) di encode kedua. |

Status job setelah render diturunkan dari render **terbaru tiap segmen**:
masih ada yang berjalan → `running`; semua gagal → `failed`; selain itu
`done` (dengan jumlah yang gagal di pesan).

## Unggahan berkas

`POST /jobs` (stage `upload`) → `POST /uploads/init` → `PUT
/uploads/{id}/parts/{n}` per 10 MB → `POST /uploads/{id}/complete`
(gabung, catat `source_media`, mulai ingest). Potongan ditulis langsung ke
disk (`output/uploads/`). `init` untuk job + ukuran yang sama mengembalikan
potongan yang sudah diterima, jadi unggahan dapat dilanjutkan.

## Keamanan

API tanpa login, jadi hanya mendengarkan `127.0.0.1`, dan
`TrustedHostMiddleware` menolak header `Host` selain localhost (DNS
rebinding). Kunci API penyedia AI dan cookies YouTube dienkripsi AES-256-GCM
dengan `TOKEN_ENCRYPTION_KEY`; kunci tersimpan tidak pernah dikirim ke base
URL yang berbeda dari tempat ia disimpan. URL YouTube disimpan dalam bentuk
kanonik dan diteruskan ke yt-dlp setelah `--`.

## Struktur direktori

```
package.json          npm workspaces (sisi JS) — lockfile tunggal di sini
pyproject.toml        satu paket Python; extra [app] dan [dev]
start.cmd, stop.cmd   menjalankan/menghentikan

apps/api/             FastAPI (router, model SQLAlchemy)
apps/worker-light/    ingest, STT, scoring AI, caption AI
apps/worker-render/   FFmpeg, MediaPipe, reframing, overlay
apps/web/             Next.js (workspace npm @clipper/web)
packages/shared/      kode bersama (dispatcher, DB, storage, AI provider, reframe, subtitle)
scripts/              setup & launcher Windows
docs/                 dokumentasi ini

.libs/ffmpeg/         FFmpeg + ffprobe           ┐
.models/              Whisper + face_landmarker  │ tidak di-commit,
output/               SQLite + media + klip      │ dibuat otomatis
.work/                ruang kerja per tahap      │
.venv-win/            virtualenv                 │
node_modules/         dependensi npm (di root)   ┘
```

Kode `apps/*` diimpor lewat `PYTHONPATH` (lihat `scripts\run-api.cmd` dan
`pyproject.toml` bagian pytest), bukan dipasang sebagai paket.

## Pengguna tunggal

Satu baris pengguna lokal (`00000000-0000-4000-8000-000000000001`) dibuat
otomatis saat startup karena beberapa tabel punya foreign key ke `users.id`.
Basis data boleh dikosongkan kapan saja.

Detail skema dan riwayat keputusan: [memory/architecture.md](../memory/architecture.md),
[memory/decisions.md](../memory/decisions.md).
