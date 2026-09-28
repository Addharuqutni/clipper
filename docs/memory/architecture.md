# Architecture — ClipperAI

> **Catatan 2026-09-27 (T9):** arsitektur yang berlaku adalah **lokal satu
> proses**. Bagian lama (3 container, Redis/Celery, PostgreSQL, R2/MinIO,
> presigned upload) sudah dihapus dari kode dan tidak lagi menjadi acuan;
> ringkasannya ada di §5. Arsitektur ringkas sehari-hari:
> [pengembangan/arsitektur.md](../pengembangan/arsitektur.md).

Arsitektur dari TECH_SPEC §1 (mode lokal), tech stack final §2, dan skema DB §3.
Nama tabel di sini **sama persis** dengan TECH_SPEC §3.

Sumber: TECH_SPEC §1, §1.1, §2, §3; T8; T9.

---

## 1. Arsitektur Lokal (satu proses)

```
┌────────────────────────────────────────────────────────┐
│  Next.js (:3000)                                       │
└──────────────────────────┬─────────────────────────────┘
                           │ HTTP REST + SSE (127.0.0.1)
                           ▼
┌────────────────────────────────────────────────────────┐
│  FastAPI (:8000, hanya 127.0.0.1)                      │
│  · clipper_shared.dispatcher: thread pool per tahap    │
│      ingest (INGEST_WORKERS=2) · stt (STT_SLOTS=1)     │
│      · render (RENDER_SLOTS=1)                         │
│  · LocalEventBus: status job → SSE                     │
│  · clipper_shared.maintenance: reconcile + purge       │
└──────────────┬──────────────────────────┬──────────────┘
               ▼                          ▼
   SQLite output/clipper.db       Disk output/ (raw, render, clips)
```

**Ukuran pool = batas paralelnya.** Sejak T9 tidak ada semaphore Redis;
pekerjaan yang belum dapat giliran menunggu di antrean pool-nya. Pool terpisah
membuat antrean render yang panjang tidak menahan job baru di tahap ingest.

**Status job** ditulis ke SQLite oleh `clipper_shared.worker_events.emit`,
lalu disiarkan ke koneksi SSE di proses yang sama. `emit` melempar
`JobCanceled` bila job dibatalkan/dihapus, sehingga task berhenti di titik
periksa berikutnya.

**Saat startup** (`app/main.py`): skema SQLite dibuat/diselaraskan (tanpa
Alembic), job/render yang masih `queued`/`running` ditandai gagal (sisa sesi
yang terputus), ruang kerja sementara disapu, dan pembersihan media kedaluwarsa
dijadwalkan tiap jam (retensi 48 jam — `clipper_shared.maintenance`).

Tanpa PostgreSQL, Redis, Celery, WSL, maupun Docker. Dijalankan dengan
`npm run dev` atau `start.cmd` (satu jendela).

**Tahap pipeline:** `ingest` → `transcribe` → `analyze` → `render` (ditulis
worker sebagai kata kerja dasar: `ingest`, `transcribe`, `analyze`, `render`,
plus `upload`/`done`).

Sumber: TECH_SPEC §1, §2, §4.3; T8; T9.

---

## 2. Tech Stack Final

| Layer | Teknologi | Alasan / Catatan kunci |
|---|---|---|
| **Frontend** | Next.js 15 (App Router, TS), Tailwind CSS | Uploader client-side; satu pengguna lokal |
| **Upload** | `react-dropzone` + endpoint lokal per potongan | Resume via potongan yang sudah diterima server (`received_parts`) |
| **API** | FastAPI, Pydantic v2, SQLAlchemy 2.0 (async), `sse-starlette` | Stateless; tidak pernah menyentuh FFmpeg dari event loop |
| **Queue** | `ThreadPoolExecutor` per tahap (`clipper_shared.dispatcher`) | Pool: `ingest`, `stt`, `render`; ukuran pool = batas paralel |
| **DB** | SQLite via `aiosqlite` (`output/clipper.db`) | Metadata, transkrip JSONB, token terenkripsi |
| **Storage** | Disk lokal (`output/`) | Raw, render, overlay, font; klip final di `output/clips/` |
| **STT** | `faster-whisper` (CTranslate2, int8), default `small` | Dijalankan di pool `stt`; model di-cache lokal |
| **Diarization** | ❌ **DITUNDA** (lihat `technical-debt.md`) | `pyannote` terlalu berat di CPU. Diganti heuristik energi + gap |
| **LLM Scoring** | Gemini 2.5 Flash (default), fallback Claude Haiku; structured JSON output | Prompt + skema Pydantic ketat; ada fallback heuristik lokal |
| **Video** | FFmpeg **>= 7** (`libx264`, `libass`), OpenCV, MediaPipe Face Landmarker | Subtitle dibakar sebagai ASS; lihat `constraints.md` §3 |
| **Publishing** | ❌ Dihapus (D5). Unduh MP4 lalu unggah manual | — |
| **YouTube** | `yt-dlp` + PO token provider | Maksimum resolusi 1080p; risiko IP block = risiko #3 di PRD |

### Penyimpangan sadar dari PRD §6

| PRD menawarkan | Dipilih | Alasan |
|---|---|---|
| FastAPI **atau** Node.js | **FastAPI saja** | Pipeline AI seluruhnya Python; Node hanya menambah hop tanpa manfaat |
| Celery / **BullMQ** | **Thread pool in-process** (T9) | Satu mesin, satu pengguna; tidak ada broker yang perlu dijaga |
| "MediaPipe" polos | MediaPipe tanpa ASD | MediaPipe **tidak punya** active-speaker detection |

### Aturan pin versi

```
Python   : 3.12–3.14  (lokal Windows: 3.14.6 di .venv-win — T8/T9)
FFmpeg   : >= 7.x  — WAJIB dengan libx264 DAN libass
                     (libass diperlukan untuk subtitle karaoke; banyak
                      paket FFmpeg distro tidak menyertakannya)
                     Lokal: build BtbN di .libs/ffmpeg/ (diunduh setup.cmd)
Node     : 22 LTS  (khusus frontend; host 24 jalan dengan peringatan)
```

Cek cepat: `ffmpeg -version | grep -E 'libx264|libass'` harus memunculkan keduanya.

> Detail environment terverifikasi ada di `constraints.md` — file itu yang
> menang untuk fakta environment.

Sumber: TECH_SPEC §2, §1.1; versi aktual dari probe host (`constraints.md`).

---

## 3. Skema Database

Skema dikelola oleh aplikasi **saat start** (`apps/api/app/db/session.py`):
tabel dibuat, dan tabel yang skemanya tertinggal dari model dibangun ulang
dengan datanya tetap utuh. **Tanpa Alembic** sejak T9 — tidak ada migrasi
manual.

```
users               (id, email, hashed_password, plan, created_at)   -- historis, tidak dipakai (satu pengguna lokal tanpa login)
jobs                (id, user_id, source_type[upload|youtube], source_url,
                     status, stage, progress, error, created_at, updated_at)
source_media        (id, job_id, r2_key, size_bytes, duration_s, codec,
                     width, height, upload_id, expires_at)
transcripts         (id, job_id, language, words JSONB, speakers JSONB,
                     full_text, model_used)
segments            (id, job_id, start_s, end_s, score, label,
                     hook_score, completeness, emotional_arc, reason,
                     status[proposed|selected|rejected])
renders             (id, segment_id, kind[preview|final], r2_key, preset,
                     subtitle_style JSONB, status, duration_ms, size_bytes)
subtitle_presets    (id, user_id, name, style JSONB)   -- warna, outline, box, posisi
social_accounts     (id, user_id, platform, encrypted_token, refresh_token,
                     expires_at, scopes)                -- tidak dipakai (D5), dibiarkan
scheduled_posts     (id, user_id, render_id, platform, caption, hashtags,
                     scheduled_at, status)              -- tidak dipakai (D5), dibiarkan
job_events          (id, job_id, stage, message, payload JSONB, created_at)
```

**Jumlah tabel di TECH_SPEC: 10.** Nama di kode sama persis; `r2_key`
dipertahankan sebagai nama kolom/kunci meski nilainya kini menunjuk **berkas
lokal** di `output/` (`clipper_shared.storage`). Di luar daftar itu, kode juga
menambah tabel katalog: `ai_provider_settings`, `font_assets`,
`overlay_assets`, `segment_overlays`.

### Ringkasan per tabel

| Tabel | Peran | Kolom kunci |
|---|---|---|
| `jobs` | Satu unit pekerjaan ingest→render | `status`, `stage`, `progress`, `error` |
| `source_media` | Media mentah di disk lokal | `r2_key`, `upload_id`, **`expires_at`** (dasar retensi 48 jam) |
| `transcripts` | Hasil ASR | `words` JSONB (word-level), `speakers` JSONB, `model_used` |
| `segments` | Segmen klip hasil scoring | `score`, `label`, `hook_score`, `completeness`, `emotional_arc`, `status` |
| `renders` | Artefak render | `kind` (`preview`/`final`), `subtitle_style` JSONB, `preset` |
| `subtitle_presets` | Preset gaya subtitle milik user | `style` JSONB |
| `social_accounts` | Kredensial OAuth terenkripsi | `encrypted_token`, `refresh_token` — tabel tidak dipakai sejak D5 (publishing dihapus) |
| `scheduled_posts` | Jadwal posting | `caption`, `hashtags`, `scheduled_at` — schema-ready, belum aktif |
| `job_events` | Log per-tahap untuk SSE & audit | `stage`, `message`, `payload` JSONB |

### Indeks wajib

```
jobs(user_id, created_at DESC)
segments(job_id, score DESC)
job_events(job_id, created_at)
```

### Keamanan token

Rahasia pihak ketiga (kini: API key penyedia AI) disimpan sebagai **AES-256-GCM**
via `cryptography`, key dari env/KMS — **bukan** Fernet tanpa AAD. Rencana rotasi
key masuk Sprint 5. Token OAuth sosial tidak lagi relevan sejak D5.

Sumber: TECH_SPEC §3.

---

## 4. Alur Data (end-to-end, ringkas)

1. Browser membuat job, lalu mengunggah media per potongan ke **API lokal**
   (atau API mengunduh dari YouTube via `yt-dlp`).
2. `jobs` row dibuat di SQLite; task `ingest` masuk pool `ingest`.
3. `ingest`: resolve sumber (YouTube atau berkas yang sudah ada di disk),
   normalisasi audio 16 kHz mono WAV + mezzanine.
4. `transcribe`: STT (`faster-whisper`) → `transcripts` + `segments`.
5. `analyze`: LLM scoring → `segments` siap direview.
6. `render`: FFmpeg crop 9:16 + burn subtitle ASS → `renders` di `output/`.
7. API mengirim progres via SSE (`job_events`) ke Review Studio.
8. Export final on-demand → berkas di `output/clips/`, diunduh pengguna.
   `POST /jobs` menolak dengan 422 bila penyedia AI belum siap (cek
   `resolve_provider` yang sama dengan worker).

Sumber: TECH_SPEC §1, §2, §3, §6; `apps/web/app/jobs/[id]/page.tsx`.

---

## 5. Riwayat (dibatalkan oleh T9)

Desain awal memakai **3 container** (A: API + auth + presign; B: worker-light
untuk ingest/STT/scoring; C: worker-render untuk FFmpeg/MediaPipe) dengan
PostgreSQL 16 + Redis 7 sebagai broker, Cloudflare R2 sebagai storage
(presigned multipart upload), queue bernama `ingest`/`transcribe`/`analyze`/
`render`/`maintenance`, dan beat untuk retensi. Rencana itu **dibatalkan
seluruhnya oleh T9**: mode distributed dihapus dari kode, dan arsitektur yang
berlaku adalah satu proses lokal di atas SQLite + disk.

Sumber: TECH_SPEC §1 (mode terdistribusi); `decisions.md` D3, T2, T9.

---

## 6. BELUM DITENTUKAN

- **BELUM DITENTUKAN** — Nilai `RENDER_SLOTS`/`STT_SLOTS` final untuk host ini.
- **BELUM DITENTUKAN** — Penyedia & anggaran proxy residensial untuk ingest YouTube.
- **BELUM DITENTUKAN** — Anggaran LLM scoring (Gemini Flash vs Haiku sebagai default,
  panjang transkrip per permintaan).
- **BELUM DITENTUKAN** — Model bisnis/tier dan perlu-tidaknya watermark.
- **BELUM DITENTUKAN** — Skema key management/KMS konkret untuk AES-256-GCM
  (hanya disebut "key dari env/KMS").

Sumber: TECH_SPEC §8, §3; probe host (`constraints.md`); T9.
