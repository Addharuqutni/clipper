# Architecture — ClipperAI

> **Catatan 2026-09-27 (T9):** bagian tentang 3 container, Redis/Celery, PostgreSQL, dan R2/MinIO adalah desain awal dan **sudah dihapus dari kode**. Arsitektur yang berlaku: [pengembangan/arsitektur.md](../pengembangan/arsitektur.md).

Arsitektur dari TECH_SPEC §1, tech stack final §2, dan skema DB §3.
Semua nama tabel di sini **sama persis** dengan TECH_SPEC §3.

Sumber: TECH_SPEC §1, §1.1, §2, §3.

---

## 1. Diagram 3-Container (deployment Docker/VPS)

```
┌────────────────────────┐
│  Next.js (browser)     │
│  · Uploader multipart  │───┐
│  · Review Studio       │   │ presigned PUT langsung ke R2
│  · SSE progress        │   │ (bypass API server)
└───────────┬────────────┘   │
            │ REST + SSE     │
            ▼                │
┌────────────────────────┐   │
│ FastAPI (container A)  │   │
│ · auth, jobs, presign  │   │
│ · TIDAK render/STT     │   │
└───┬────────────────┬───┘   │
    │                │       │
    ▼                ▼       ▼
┌─────────┐   ┌──────────┐  ┌──────────────────┐
│ Postgres│   │  Redis 7 │  │ Cloudflare R2    │
│ 16      │   │ broker + │  │ (S3-compatible)  │
│+pgvector│   │ pub/sub  │  │ lifecycle rules  │
└─────────┘   └────┬─────┘  └──────────────────┘
                   │               ▲
        ┌──────────┴──────────┐    │
        ▼                     ▼    │
┌────────────────┐   ┌────────────────────┐
│ worker-light   │   │ worker-render      │
│ (container B)  │   │ (container C)      │
│ ingest, STT,   │   │ FFmpeg + MediaPipe │
│ LLM scoring    │   │ concurrency=1      │
│ concurrency=2  │   │ cpu-limit ketat    │
└────────────────┘   └────────────────────┘
   1–2 vCPU            2–6 vCPU, dedicated
```

**Prinsip pemisahan:** container A/B/C **terpisah secara fisik**, karena FFmpeg
dapat menghabiskan seluruh core dan akan membekukan API bila berbagi. Ini
konsekuensi langsung dari **D3** (VPS self-managed, tanpa auto-scale platform).

### Pemetaan container → queue

| Container | Peran | Queue | Concurrency |
|---|---|---|---|
| **A — api** | auth, jobs, presign, SSE. Stateless, **tidak pernah** menyentuh FFmpeg | — | — |
| **B — worker-light** | ingest, STT (`faster-whisper`), LLM scoring | `ingest`, `transcribe`, `analyze` | `2` |
| **C — worker-render** | FFmpeg + MediaPipe, crop 9:16, burn subtitle | `render` | `1` (`--max-tasks-per-child=1`) |
| — (beat) | retensi 48 jam, housekeeping | `maintenance` | — |

Sumber: TECH_SPEC §1, §2 (baris Queue), §4.3.

### Mode lokal: Windows Standalone (T8)

Di workstation Windows, ketiga container dilebur ke **satu proses API**:

```
Next.js dev (:3000) ──REST+SSE──▶ FastAPI (:8000, STANDALONE=true)
                                   · worker-light + worker-render dijalankan
                                     ThreadPoolExecutor (clipper_shared.dispatcher)
                                   · LocalEventBus (SSE), LocalSemaphore (slot)
                                   ├─▶ SQLite  output/clipper.db
                                   └─▶ disk    output/ (raw, overlay, render, clips)
```

Tanpa PostgreSQL, Redis, Celery, WSL, maupun Docker. Dijalankan dengan
`npm run dev` atau `start.cmd` (satu jendela). Pemisahan fisik A/B/C hanya
berlaku di deployment, karena lokal tidak ada pengguna lain yang API-nya bisa
membeku.

---

## 2. Tech Stack Final

| Layer | Teknologi | Alasan / Catatan kunci |
|---|---|---|
| **Frontend** | Next.js 15 (App Router, TS), Tailwind CSS v4, shadcn/ui, TanStack Query, Zustand | SSR untuk halaman marketing/SEO; uploader wajib client-side |
| **Upload** | `@aws-sdk/client-s3` + `@aws-sdk/lib-storage` (browser), `react-dropzone`, IndexedDB untuk state resume | Multipart 10 MB/part, resume via `ListParts`, retry eksponensial |
| **API** | FastAPI, Pydantic v2, SQLAlchemy 2.0 (async), Alembic, `sse-starlette` | Stateless; tidak pernah menyentuh FFmpeg |
| **Queue** | Celery 5 + Redis 7 (broker/backend), Flower (monitoring) | Queue terpisah: `ingest`, `transcribe`, `analyze`, `render`, `maintenance` |
| **DB** | PostgreSQL 16 + `pgvector` | Metadata, transkrip JSONB, token terenkripsi |
| **Storage** | Cloudflare R2 (S3-compatible) | Egress gratis = krusial untuk distribusi klip; lifecycle rule native |
| **STT** | `faster-whisper` (CTranslate2, int8), default `small` | Dijalankan di `worker-light`; model di-cache di volume persisten |
| **Diarization** | ❌ **DITUNDA** (lihat §5.3) | `pyannote` terlalu berat di CPU. Diganti heuristik energi + gap |
| **LLM Scoring** | Gemini 2.5 Flash (default), fallback Claude Haiku; structured JSON output | Prompt + skema Pydantic ketat; ada fallback heuristik lokal |
| **Video** | FFmpeg **>= 7** (`libx264`, `libass`), OpenCV, MediaPipe, PySceneDetect, `boxmot`/ByteTrack | Subtitle dibakar sebagai **ASS karaoke** (`\k`), bukan overlay per-frame. WSL punya 8.0.1 — lihat `constraints.md` §3 |
| **Publishing** | ❌ Dihapus (D5). Unduh MP4 lalu unggah manual | — |
| **YouTube** | `yt-dlp` + PO token provider + rotasi proxy residensial | Risiko IP block = risiko #3 di PRD |
| **Infra** | Hetzner CCX (dedicated vCPU) + Dokploy + Traefik; GitHub Actions; Sentry | `cpu.max` cgroup per container; lihat §4 |
| **Backup** | `pg_dump` harian → R2 + restore drill terjadwal | VPS tanpa backup teruji = satu disk gagal, semua hilang |

### Penyimpangan sadar dari PRD §6

| PRD menawarkan | Dipilih | Alasan |
|---|---|---|
| FastAPI **atau** Node.js | **FastAPI saja** | Pipeline AI seluruhnya Python; Node hanya menambah hop tanpa manfaat |
| Celery / **BullMQ** | **Celery** | Worker berat adalah FFmpeg + model ML Python native; BullMQ memaksa Node worker yang harus shell-out ke Python |
| "MediaPipe" polos | Reframing tanpa MediaPipe ASD | MediaPipe **tidak punya** active-speaker detection (lihat §5.2) |

### Aturan pin versi

```
Python   : 3.12.x  (image container, base python:3.12-slim-bookworm)
           3.14.6  (lokal Windows, .venv-win — T8)
FFmpeg   : >= 7.x  — WAJIB dengan libx264 DAN libass
                     (libass diperlukan untuk subtitle karaoke; banyak
                      paket FFmpeg distro tidak menyertakannya)
                     Lokal: build BtbN di .libs/ffmpeg/ (diunduh setup.cmd)
Node     : 22 LTS  (khusus frontend; host 24 jalan dengan peringatan)
Postgres : 16      (hanya deployment)
Redis    : 7.2     (hanya deployment)
```

Cek cepat: `ffmpeg -version | grep -E 'libx264|libass'` harus memunculkan keduanya.

> Detail environment terverifikasi ada di `constraints.md` — file itu yang
> menang untuk fakta environment.

Sumber: TECH_SPEC §2, §1.1; versi aktual dari probe host (`constraints.md`).

---

## 3. Skema Database

Schema dikelola dengan **Alembic**, dibuat di **Sprint 0**. Semua tabel di bawah
ini adalah daftar lengkapnya — tidak ada tabel lain di dokumen sumber.

```
users               (id, email, hashed_password, plan, created_at)
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
                     scheduled_at, status)              -- schema-ready, belum aktif
job_events          (id, job_id, stage, message, payload JSONB, created_at)
```

**Jumlah tabel: 10.** Nama tabel (urut abjad): `job_events`, `jobs`, `renders`,
`scheduled_posts`, `segments`, `social_accounts`, `source_media`,
`subtitle_presets`, `transcripts`, `users`.

### Ringkasan per tabel

| Tabel | Peran | Kolom kunci |
|---|---|---|
| `users` | Akun & auth | `email`, `hashed_password`, `plan` |
| `jobs` | Satu unit pekerjaan ingest→render | `status`, `stage`, `progress`, `error` |
| `source_media` | Objek media mentah di R2 | `r2_key`, `upload_id`, **`expires_at`** (dasar retensi 48 jam) |
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

Deployment (A/B/C terpisah):

1. Browser minta presigned multipart ke **A** → upload part langsung ke **R2**
   (bypass API server).
2. `jobs` row dibuat di Postgres; task `ingest` masuk Redis.
3. **B** (`worker-light`): resolve sumber (YouTube via `yt-dlp` atau R2),
   normalisasi audio 16 kHz mono WAV + mezzanine, lalu STT + LLM scoring.
4. Hasil → `transcripts` + `segments`.
5. **C** (`worker-render`): FFmpeg crop 9:16 + burn ASS karaoke → `renders`.
6. **A** mengirim progres via SSE (`job_events`) ke Review Studio.
7. Export final on-demand → presigned download URL dari R2.

Lokal Standalone: urutan tahapnya sama, tetapi job disimpan di SQLite, task
dijalankan thread pool di proses API, storage di `output/`, dan klip akhir
ditulis ke `output/clips/`. `POST /jobs` menolak dengan 422 bila penyedia AI
belum siap (cek `resolve_provider` yang sama dengan worker).

Sumber: TECH_SPEC §1, §2, §3, §6.

---

## 5. BELUM DITENTUKAN

- **BELUM DITENTUKAN** — Spesifikasi VPS Hetzner (vCPU/RAM/tipe instance);
  menentukan `RENDER_SLOTS` dan aman-tidaknya 1 render + 1 STT paralel.
- **BELUM DITENTUKAN** — Penyedia & anggaran proxy residensial untuk ingest YouTube.
- **BELUM DITENTUKAN** — Anggaran LLM scoring (Gemini Flash vs Haiku sebagai default,
  panjang transkrip per permintaan).
- **BELUM DITENTUKAN** — Model bisnis/tier dan perlu-tidaknya watermark.
- **BELUM DITENTUKAN** — Skema key management/KMS konkret untuk AES-256-GCM
  (hanya disebut "key dari env/KMS").
- **BELUM DITENTUKAN** — Cara memasang Docker di mesin deployment/CI agar
  `docker-compose.yml` diuji eksekusi. Development lokal tidak butuh Docker (T8).

Sumber: TECH_SPEC §8, §4.1, §3; probe host (`constraints.md` §4); T8.
