# ClipperAI — Technical Specification & Task Plan

**Version:** 1.0.0
**Derived from:** [`PRD.md`](PRD.md) v1.0.0 (MVP)
**Status:** Frozen for execution (no code scaffolded yet)

> **Catatan 2026-09-27 (T9):** dokumen ini adalah **spesifikasi historis**. Semua bagian **deployment** (3 container, Docker/VPS, Dokploy/Traefik, Celery/Redis, PostgreSQL, R2 presigned upload, auth) **superseded oleh keputusan T9**: aplikasi berjalan **lokal saja** (satu proses, SQLite + disk). Bagian tersebut dipertahankan sebagai riwayat dan **bukan instruksi**. Kebenaran operasional ada di [`../memory/constraints.md`](../memory/constraints.md); detail keputusan di [`../memory/decisions.md`](../memory/decisions.md#t9--lokal-saja-docker-dan-mode-distributed-dihapus).
**Date:** 2026-09-20

---

## 0. Scope Lock (Keputusan yang Sudah Dikunci)

Empat keputusan pengguna yang mengikat seluruh dokumen ini:

| # | Keputusan | Konsekuensi |
|---|---|---|
| D1 | **STT self-host, CPU-only** (`faster-whisper`) | OKR "30 menit → ≤5 menit" **TIDAK TERCAPAI**. Metrik diganti (lihat §0.1). |
| D2 | ~~Publishing ditunda~~ — **digantikan D5 (2026-09-26): publishing dihapus**, produk download-only | Tidak ada OAuth/posting ke platform sosial. Lihat [`../memory/decisions.md`](../memory/decisions.md) D5. |
| D3 | ~~**VPS self-managed** (Hetzner + Dokploy)~~ — **superseded oleh T9**: lokal saja, tanpa Docker/deployment | Tidak ada auto-scale platform. Wajib admission control + QoS CPU + backup teruji. |
| D4 | **Hanya dokumen**, tanpa scaffold kode | Dokumen ini adalah deliverable. Baris "Scaffold" di Sprint 0 adalah tugas, bukan pekerjaan yang sudah dilakukan. |

### 0.1 Koreksi Target Metrik (menggantikan PRD §1.3)

| Metrik PRD | Status setelah D1 | Metrik Pengganti |
|---|---|---|
| Processing 30 menit ≤ 5 menit | ❌ Tidak realistis di CPU | **Time-to-first-clip** ≤ 25 menit (video 30 menit, `small`/int8, 4 vCPU). End-to-end penuh ~50–90 menit. |
| Reframing accuracy ≥ 85% | ⚠️ Bergantung heuristik | Tetap ≥ 85%, diukur pada set uji berlabel 20 klip. Lihat §5.2. |
| Export & Publish Rate ≥ 65% | ✅ Tetap (sebagai Export Rate) | Diukur pada klip yang direkomendasikan AI dan diunduh pengguna. |
| Upload reliability ≥ 98% (>1 GB) | ✅ Tetap | Tetap, dengan multipart resume. |

**Angka waktu transkripsi CPU (`faster-whisper`, int8):** `small` ≈ 0.5–0.9× realtime, `medium` ≈ 0.25–0.5× realtime, `large-v3-turbo` ≈ 0.4–0.7×. Semua bergantung vCPU. Default MVP = `small` int8.

---

## 1. Arsitektur Sistem

ClipperAI dirancang dengan arsitektur **Dual-Mode** yang fleksibel:

### Mode 1: Terdistribusi / Multi-Container (Produksi / VPS / Docker) — **DIHAPUS oleh T9 (riwayat)**

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
│ · identity, jobs, SSE  │   │
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

**Prinsip pemisahan:** container A/B/C terpisah secara fisik di lingkungan server, karena FFmpeg dapat menghabiskan seluruh core dan membekukan API bila berbagi.

### Mode 2: Windows Native Standalone (Workstation Lokal — Zero External Dependencies)

Untuk mempermudah penggunaan di workstation Windows tanpa perlu menyalakan Docker, WSL, PostgreSQL, atau Redis:

```
┌────────────────────────────────────────────────────────┐
│  Next.js Frontend (:3000)                              │
└──────────────────────────┬─────────────────────────────┘
                           │ HTTP REST + SSE
                           ▼
┌────────────────────────────────────────────────────────┐
│  FastAPI Backend (:8000) [STANDALONE=true]             │
│  · Unified Task Dispatcher (clipper_shared.dispatcher) │
│  · In-Process ThreadPool Background Runner             │
│  · In-Memory SSE Event Bus (LocalEventBus)             │
│  · In-Process Admission Semaphore (LocalSemaphore)     │
└──────────────┬──────────────────────────┬──────────────┘
               │                          │
               ▼                          ▼
┌──────────────────────────────┐   ┌─────────────────────┐
│  SQLite + aiosqlite          │   │  Local Storage      │
│  output/clipper.db           │   │  output/<judul>-<id>/        │
│  (JSONB & UUID compatibility)│   │  output/raw/ ...             │
└──────────────────────────────┘   └─────────────────────┘
```

- **Database:** SQLite tersemat otomatis via `aiosqlite` di `output/clipper.db`. Disediakan proxy custom sqlite3 dan compiler extensions `@compiles(JSONB, "sqlite")` & `@compiles(PGUUID, "sqlite")` agar skema SQLAlchemy PostgreSQL dapat berjalan tanpa migrasi terpisah.
- **Task Runner:** `ThreadPoolExecutor` internal yang mengalirkan task asinkron (`ingest` -> `transcribe` -> `analyze` -> `render`) secara bertahap tanpa Celery atau Redis broker.
- **Event Bus:** `LocalEventBus` in-memory yang menyalurkan pembaruan tahapan secara real-time via SSE langsung ke frontend browser.
- **Storage:** Seluruh asset tersimpan di disk lokal (`output/`), dengan video sumber dan klip hasil sebuah job ditaruh bersama di `output/<judul>-<id>/`.

---

### 1.1 Dev Environment (diverifikasi pada host Windows)

Kondisi host Windows saat ini:
- Windows 10/11, Python **3.14.6** didukung via virtualenv lokal (`.venv-win`). Dependensi ML seperti `faster-whisper`, `ctranslate2`, `mediapipe`, dan `opencv-python-headless` telah menyediakan wheel binary `win_amd64` yang bekerja stabil di host.
- FFmpeg 7+ dengan dukungan `libx264` dan `libass` disediakan di `.libs/ffmpeg/`.
- Node.js 24 LTS untuk frontend Next.js 15.

**Jalur eksekusi yang didukung:**
1. **Windows Native Standalone (satu-satunya jalur sejak T9):**
   Cukup jalankan `npm run dev` (atau klik dua kali `start.cmd`). Semua layanan berjalan di satu terminal dan lokal SELALU Standalone (SQLite, tanpa PostgreSQL/Redis).
2. ~~**Docker Compose / VPS (Produksi)**~~ — **superseded oleh T9**: tidak ada container, PostgreSQL, Redis, maupun deployment server.

**Aturan pin versi (historis — tidak ada image container produksi sejak T9):**

```
Python   : 3.12.x  (base image python:3.12-slim-bookworm)
FFmpeg   : 7.x     — WAJIB dibangun dengan libx264 DAN libass
Node     : 22 LTS  (khusus frontend)
Postgres : 16
Redis    : 7.2
```

Cek cepat sebelum mulai: `ffmpeg -version | grep -E 'libx264|libass'` (atau PowerShell `Select-String libass`) harus memunculkan keduanya.

---

## 2. Tech Stack Final

| Layer | Teknologi | Alasan / Catatan kunci |
|---|---|---|
| **Frontend** | Next.js 15 (App Router, TS), Tailwind CSS v4, shadcn/ui, TanStack Query, Zustand | SSR untuk halaman marketing/SEO; uploader wajib client-side |
| **Upload** | `@aws-sdk/client-s3` + `@aws-sdk/lib-storage` (browser), `react-dropzone`, IndexedDB untuk state resume | Multipart 10 MB/part, resume via `ListParts`, retry eksponensial |
| **API** | FastAPI, Pydantic v2, SQLAlchemy 2.0 (async), Alembic, `sse-starlette` | Stateless; tidak pernah menyentuh FFmpeg |
| **Queue** | In-Process ThreadPool per tahap (Standalone) — ~~Celery 5 + Redis 7~~ (superseded T9) | Tahap: `ingest`, `transcribe`, `analyze`, `render`; ukuran pool = batas paralel |
| **DB** | SQLite via `aiosqlite` (Standalone) — ~~PostgreSQL 16 + pgvector~~ (superseded T9) | Metadata, transkrip JSONB, token terenkripsi |
| **Storage** | Local filesystem (`output/`) — ~~Cloudflare R2~~ (superseded T9) | Raw, render, overlay, font; video sumber + klip final di `output/<judul>-<id>/` |
| **STT** | `faster-whisper` (CTranslate2, int8), default `small` | Dijalankan di `worker-light`; model di-cache di volume persisten |
| **Diarization** | ❌ **DITUNDA** (lihat §5.3) | `pyannote` terlalu berat di CPU. Diganti heuristik energi + gap |
| **LLM Scoring** | Gemini 2.5 Flash (default), fallback Claude Haiku; structured JSON output | Prompt + skema Pydantic ketat; **parsing toleran-kerusakan, temperature berbasis niat, pertahanan prompt injection, dan permintaan berlebih `num_clips + 3`** — lihat §5.4 |
| **Video** | FFmpeg 7 (`libx264`, `libass`), OpenCV, MediaPipe **Face Landmarker** (`mediapipe.tasks`, bukan legacy `solutions`), PySceneDetect | Reframing: landmark bibir → skor pembicara aktif (§5.2). Subtitle: **ASS satu-event-per-kata** dengan override warna inline, bukan tag `\k` (§5.2.1). `boxmot`/ByteTrack **dibatalkan** |
| **Publishing** | ❌ Dihapus (D5). Pengguna mengunduh MP4 lalu mengunggah manual | — |
| **YouTube** | `yt-dlp` (maksimum resolusi download 1080p) + PO token provider | Batas download 1080p (`bv*[height<=1080]+ba`) menjaga efisiensi CPU; risiko IP block = risiko #3 di PRD |
| **Infra** | ~~Hetzner CCX + Dokploy + Traefik; `cpu.max` cgroup~~ (superseded T9) + GitHub Actions | Aplikasi lokal satu proses; CI menjalankan test/lint |
| **Backup** | ~~`pg_dump` harian → R2 + restore drill~~ (superseded T9) | Skema backup lokal `output/` BELUM DITENTUKAN |

**Catatan pemilihan yang sengaja menyimpang dari PRD §6:**

- PRD menawarkan "FastAPI **atau** Node.js". Dipilih **FastAPI saja** untuk backend — pipeline AI seluruhnya Python, memakai Node hanya akan menambah hop tanpa manfaat.
- PRD menawarkan "Celery / BullMQ". ~~Dipilih **Celery**~~ — superseded T9: worker berat berjalan sebagai **thread pool in-process** per tahap (`clipper_shared.dispatcher`), tanpa broker.
- Reframing tidak memakai "MediaPipe" secara polos: MediaPipe **tidak punya** active-speaker detection (lihat §5.2).

---

## 3. Skema Database (Alembic, Sprint 0 — ~~Alembic~~ dihapus oleh T9; skema dikelola saat startup)

```
users               (id, email, hashed_password, plan, created_at)
jobs                (id, user_id, source_type[upload|youtube], source_url,
                     video_title, status, stage, progress, error,
                     created_at, updated_at)
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

**Catatan `jobs.video_title`:** judul video untuk dashboard, `NULL` bila belum
diketahui. Sumbernya mengikuti jenis job: **nama berkas** untuk unggahan (diisi
API saat `POST /uploads/init`) dan **`YoutubeMetadata.title`** untuk YouTube
(diisi worker saat ingest, lewat `record_source_media`). Job lama tetap sah —
pembangunan ulang tabel SQLite saat startup mengisi kolom baru dengan `NULL`.

**Catatan keamanan token (PRD §5 "Security & Compliance"):**
Rahasia pihak ketiga (kini: API key penyedia AI di `ai_provider_settings`) disimpan sebagai **AES-256-GCM** via `cryptography`, key dari env/KMS — bukan Fernet tanpa AAD. Rencana rotasi key masuk Sprint 5.

**Indeks wajib:** `jobs(user_id, created_at DESC)`, `segments(job_id, score DESC)`, `job_events(job_id, created_at)`.

**Lifecycle R2 (PRD §5) — ~~R2~~ superseded T9, retensi tetap berlaku di disk lokal:**
- Raw media: hapus 48 jam setelah render terakhir job selesai.
- Klip final: pindah ke kelas arsip setelah 14 hari.
- **Catatan penting:** lifecycle rule berbasis prefix + umur objek, sedangkan PRD meminta "48 jam setelah proses pemotongan selesai" — ini bergantung kejadian, bukan umur objek. Implementasinya sejak T9: **`clipper_shared.maintenance.purge_expired_raw_media`** (in-process, dijadwalkan API tiap jam) yang menghapus berdasarkan `source_media.expires_at` di DB.

---

## 4. Strategi VPS & QoS (konsekuensi langsung D3) — **DIHAPUS oleh T9 (riwayat)**

> Sejak T9 tidak ada VPS, container, maupun `cpu.max`. Padanan lokalnya:
> ukuran pool `RENDER_SLOTS`/`STT_SLOTS` di `clipper_shared.dispatcher`
> (lihat [`../memory/constraints.md`](../memory/constraints.md) §6).

Bagian yang paling sering diabaikan dan paling menentukan stabilitas sistem.

### 4.1 Kapasitas — menunggu angka dari Anda

Spesifikasi VPS belum dikonfirmasi. Tabel sizing berikut sebagai kerangka keputusan:

| Tipe Hetzner | vCPU | RAM | Peran yang disarankan |
|---|---|---|---|
| CX22 | 2 (shared) | 4 GB | Cukup untuk API + Postgres + Redis. Render akan menyiksa seluruh sistem. |
| CCX13 | 2 (dedicated) | 8 GB | Minimum realistis: API + light worker. Render tetap lambat. |
| CCX23 | 4 (dedicated) | 16 GB | **Sweet spot MVP** — API+DB+Redis di satu, render di satelit. |
| CCX33 | 8 (dedicated) | 32 GB | Nyaman: 1 render paralel + 1 STT paralel tanpa starvation. |

**vCPU dedicated (CCX) wajib**, bukan shared (CX): FFmpeg pada shared vCPU menghasilkan waktu render yang tak terprediksi, dan itu membuat estimasi "time-to-first-clip" tidak bermakna.

### 4.2 Admission Control — setara lokal: ukuran pool `clipper_shared.dispatcher`

~~Redis semaphore sebelum task render dieksekusi~~ (superseded T9):

```
RENDER_SLOTS=1        # pool render paralel; naikkan hanya bila kapasitas terukur
STT_SLOTS=1
```

Sejak T9 tidak ada `Retry`/semaphore: pekerjaan yang belum dapat giliran cukup
menunggu di antrean `ThreadPoolExecutor` milik pool-nya.

### 4.3 Isolasi CPU

- Container `worker-render`: batasi `cpu.max` (cgroup v2), mis. 300% dari 400% total → menyisakan 1 core untuk API.
- Panggil FFmpeg dengan `-threads N` eksplisit. **Jangan** biarkan FFmpeg memakai seluruh core — ini penyebab nomor satu API tidak responsif.
- `celery --concurrency=1 --max-tasks-per-child=1` untuk worker-render, mencegah kebocoran memori OpenCV/MediaPipe menumpuk antar job.
- Preview 540×960 (9:16): `-preset veryfast -crf 30` (render cepat untuk verifikasi dan preview di browser).
- Final export 1080×1920 Full HD (9:16): `-preset slow -crf 18` (kualitas visual tinggi untuk publikasi TikTok/Reels/Shorts).
- Batasi OpenCV `cv2.setNumThreads()` agar tidak berkelahi dengan FFmpeg memperebutkan core.

### 4.4 Yang Wajib Ada Sebelum Beta (bukan "nanti")

1. Backup `pg_dump` harian → R2, retensi 30 hari.
2. **Restore drill terjadwal** — backup yang belum pernah direstore secara efektif bukan backup.
3. Healthcheck + auto-restart Dokploy untuk setiap container.
4. Alert: queue depth melewati ambang, disk > 80%, kegagalan task > N dalam 15 menit.
5. Volume persisten untuk cache model Whisper — unduh sekali, bukan tiap deploy.

---

## 5. Penyesuaian Teknis Akibat D1 (CPU-only)

### 5.1 Abstraksi Transcriber (wajib, bukan opsional)

Ini satu-satunya hedge yang membuat D1 dapat dibalik tanpa penulisan ulang:

```python
class Transcriber(Protocol):
    def transcribe(self, audio_path: str, language: str | None) -> TranscriptResult: ...


class FasterWhisperLocal(Transcriber): ...  # default MVP


class RemoteWhisperAPI(Transcriber): ...  # cadangan, diaktifkan via env
```

Dipilih lewat `STT_BACKEND=local|remote`.

### 5.2 Reframing tanpa MediaPipe ASD

MediaPipe **tidak menyediakan** active-speaker detection; PRD §6 mengasumsikan hal ini tersedia. Namun pendekatan yang terbukti berjalan ada: **repo referensi `jipraks/yt-short-clipper` (v2, aplikasi Tauri yang sudah dipakai untuk membuat klip vertikal) memecahkan masalah ini dengan MediaPipe Face Landmarker + sinyal bukaan mulut.** Rencana di bawah mengadopsi pendekatan itu, menggantikan draf awal (BlazeFace + ByteTrack) yang lebih rapuh.

**Komponen dan nilai tuning (dari kode referensi):**

| Parameter | Nilai | Fungsi |
|---|---|---|
| Model | `vision.FaceLandmarker` (`mediapipe.tasks`, bukan legacy `solutions`) | 478 titik landmark termasuk bibir + mata |
| `num_faces` | 10 | Maksimum wajah dilacak per frame |
| `min_face_detection_confidence` | 0.35 | Ambang deteksi |
| `RunningMode` | `VIDEO` (timestamp antar-frame, bukan IMAGE) | Menjaga pelacakan antar-frame |
| Lip indices | 24 titik di area mulut (13, 14, 78, 81, …) | Hitung bukaan mulut |
| `MIN_SPEAK_RATIO` | 0.18 | Bukaan bibir / tinggi wajah di atas ini = "sedang bicara" |
| `SPEAKER_BONUS` | 2.0 | Bobot tambahan untuk wajah yang sedang bicara |
| `CONTINUITY_WEIGHT` | 0.4 | Penalti memilih wajah yang jauh dari crop sekarang (anti-lompat) |
| `EMA_ALPHA` | 0.15 | Kecepatan crop mengikuti target (makin kecil makin halus) |
| `DEADZONE_FRAC` | 0.02 | Abaikan gerakan < 2% lebar crop → anti-jitter |
| `SNAP_FRAC` | 0.35 | Lompatan > 35% lebar crop = ganti adegan → potong langsung, tanpa EMA |

**Algoritma pemilihan subjek per frame:**

```
skor = (bobot_ukuran) * (1 + SPEAKER_BONUS * sedang_bicara)
       - CONTINUITY_WEIGHT * penalti_jarak_dari_crop_sekarang
```

Ini memberi "stickiness" pada subjek: kamera tidak berpindah hanya karena wajah lain muncul sesaat. Bila tidak ada wajah terdeteksi, **pertahankan posisi crop terakhir** — jangan kembali ke tengah, karena gerakan mendadak lebih mengganggu daripada tetap.

**Dua jebakan implementasi yang wajib dihindari** (keduanya ditemukan di kode referensi sebagai komentar eksplisit):

1. **Konversi ruang warna.** OpenCV membaca frame sebagai **BGR**, MediaPipe memerlukan **RGB**. Tanpa `cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)` sebelum `detect_for_video()`, akurasi deteksi hancur dan gejalanya halus (bukan error) — hanya crop yang terlihat salah. *Implementasi ClipperAI:* frame dibaca dari FFmpeg langsung sebagai `rgb24` dan diperkecil ke lebar ≤640 px (`ANALYSIS_MAX_WIDTH`, `flags=area`) sebelum masuk pipa; landmark ternormalisasi sehingga posisi crop tetap dihitung pada ukuran sumber. Ini menghapus salinan BGR→RGB per frame di Python dan memangkas pelacakan 1080p ±40 dtk → ±14–24 dtk per 30 dtk klip pada CPU 8 thread.
2. **Deadlock saat menulis ke stdin FFmpeg.** Bila frame mentah dikirim ke stdin ffmpeg, **ffmpeg akan mati kunci** saat buffer stderr (di Windows ~64 KB) penuh, karena tidak ada yang membacanya. Wajib menjalankan **thread pembuangan stderr** yang membaca terus-menerus, plus **watchdog** (`ENCODE_STALL_TIMEOUT_S = 300`) yang mematikan ffmpeg bila tidak ada kemajuan frame, supaya `stdin.write()` tidak memblokir selamanya.

Lebar crop dihitung `min(orig_w, orig_h * 1080/1920)` — sumber yang lebih sempit dari 9:16 memakai lebar penuh, menghindari rentang negatif.

Encode: `libx264 -preset fast -crf 18 -pix_fmt yuv420p`. Audio **tidak** dicampur dalam pass yang sama: ia diekstrak ke AAC 192k secara terpisah, lalu di-*mux* dengan `-c:v copy -c:a copy -shortest`. Ini menghindari menahan seluruh video di memori.

**Pernyataan jujur:** target 85% di PRD tetap harus diukur, bukan diklaim. Angka tuning di atas adalah titik awal yang terbukti, bukan jaminan untuk semua jenis rekaman.

### 5.2.1 Subtitle ASS: koreksi pendekatan

Rencana awal di §6 Sprint 3 menyebut tag karaoke `\k`. **Repo referensi tidak memakai `\k`.** Pendekatannya: **satu event `Dialogue` per kata**, dengan potongan 4 kata diulang dan hanya kata berjalan yang diwarnai lewat override inline `{\c&H00FFFF&}KATA{\c&HFFFFFF&}`. Hasil visualnya sama (karaoke berjalan) tetapi lebih sederhana dihasilkan.

Nilai gaya yang dipakai (ditulis untuk kanvas acuan 1080×1920):

```
Fontname   : Arial Black (bisa dibundel di aplikasi, tidak perlu instalasi sistem)
Fontsize   : 65      Outline: 4      Shadow: 2
BorderStyle: 1       Alignment: 2    (bawah-tengah)
MarginL/R  : 50      MarginV: 400
ScriptType : v4.00+  WrapStyle: 0    ScaledBorderAndShadow: yes
```

**Jebakan paling mudah terlewat:** libass **meregangkan kanvas yang dideklarasikan agar memenuhi frame**. Bila `PlayResX: 1080 / PlayResY: 1920` ditulis tetap, maka pada klip non-9:16 subtitle akan gepeng (melebar 1,78×, memendek 0,56×). Karena itu resolusi nyata video **wajib di-probe** (`cv2.CAP_PROP_FRAME_WIDTH/HEIGHT`) dan dipakai sebagai `PlayResX/PlayResY`; metrik gaya lalu diskalakan dari acuan 1080×1920 (ukuran font/outline/shadow/MarginV menurut rasio tinggi; MarginL/R menurut rasio lebar).

Dua detail teknis lain:

- **Warna ASS adalah `&HAABBGGRR`** — urutan **BGR**, bukan RGB. Kuning `#FFFF00` menjadi `&H00FFFF`. Salah urutan menghasilkan warna tertukar (merah↔biru) tanpa error.
- **Format waktu ASS `H:MM:SS.cc` memakai centisecond**, bukan milidetik.
- Di Windows, path file ASS di dalam filter graph ffmpeg harus dilolosikan: garis miring dibalik **dan** titik dua di-escape (`C:/x.ass` → `C\:/x.ass`). Tanpa ini filter gagal.

### 5.3 Utang Teknis (dicatat resmi)

| Utang | Alasan ditunda | Kapan ditinjau |
|---|---|---|
| Speaker diarization (`pyannote`) | Terlalu berat di CPU | Saat GPU tersedia atau STT pindah ke API |
| Auto-scale worker berbasis beban antrean | Konsekuensi D3 (VPS) | Saat migrasi ke cloud/GPU |
| Akurasi `large-v3` penuh | Kecepatan CPU | Saat GPU tersedia |
| `ByteTrack`/`boxmot` | **Tidak diperlukan lagi** — identitas subjek dijaga oleh `CONTINUITY_WEIGHT` (§5.2) | Dibatalkan dari Sprint 3 |

### 5.4 Pengerasan Output LLM Scoring (dari repo referensi)

Bagian ini **menggantikan** rencana Sprint 2 butir 21 yang hanya menyebut "validator + guardrail". Pelajaran dari `highlight_finder.py` di repo referensi menunjukkan bahwa mempercayai LLM mengembalikan JSON yang valid adalah bentuk optimisme yang mahal.

**Strategi parsing — selamatkan, jangan gagal:**

| Masalah | Solusi |
|---|---|
| Model menulis pagar markdown ` ```json ` | Buang pagar sebelum parsing |
| **Satu** tanda kutip tak ter-escape merusak seluruh array | Jangan `json.loads` seluruh teks. Telusuri dengan `JSONDecoder().raw_decode()`, lewati objek yang gagal, **hitung berapa yang diselamatkan** dan laporkan di log |
| Model mengembalikan jumlah segmen kurang dari yang diminta | **Minta lebih: `num_clips + 3`**, lalu saring di kode. Permintaan "tepat 5" di prompt tidak dapat diandalkan |
| Model mengembalikan field `reason` alih-alih `description` | Petakan nama field (`reason` → `description`) sebelum validasi |

**Kontrol kreativitas berbasis niat:**

| Kondisi | Temperature | Alasan |
|---|---|---|
| Tanpa arahan pengguna | **1.0** | Variasi segmen diinginkan |
| Ada arahan pengguna | **0.3** | Pada 1.0, arahan yang sama dipatuhi di satu percobaan dan diabaikan di percobaan lain — tidak dapat diterima untuk fitur yang pengguna sudah mengetik instruksi spesifik |

**Pertahanan terhadap prompt injection** (wajib, karena arah pengguna adalah teks bebas):

1. Buang token placeholder dari teks pengguna: `{num_clips}`, `{transcript}`, `{video_context}`, `{user_direction}`, `{output_language}` — agar pengguna tidak bisa menyuntikkan transkrip kedua atau mengosongkan variabel.
2. Buang pagar pembatas `--- USER DIRECTION START/END ---` dari teks pengguna — agar ia tidak bisa menutup bloknya sendiri lalu berbicara sebagai prompt.
3. Batasi panjang: UI 1000 karakter, penjaga backend 2000 karakter, potong dengan ` ...`.
4. **Letakkan ulang arahan pengguna SETELAH transkrip.** Transkrip podcast 60 menit dapat melewati 100 ribu karakter; apa pun yang diletakkan sebelum itu kalah oleh efek *recency* dan arahan akan diabaikan.

**Variansi durasi dan rentang yang diminta pengguna:**

Model menyelaraskan segmen ke batas cue subtitle, sehingga durasi jarang jatuh tepat di 30–60 detik. Saringan durasi karena itu harus punya toleransi, **tetapi** bila pengguna mengetik rentang waktu eksplisit ("2:00 - 2:50", "dari 21:30 sampai 22:25"), rentang itu **dikecualikan dari saringan durasi minimum** — pengguna meminta rentang itu, jadi klip 50 detik adalah jawaban yang benar, bukan yang ditolak. Pencocokan memakai toleransi `±8 detik` karena model akan membulatkan ke batas cue terdekat.

**Kesiapan provider:**

- Klien bergaya OpenAI-compatible dengan `timeout=180`, `max_retries=4`, dan pencatatan pemakaian token per panggilan.
- **Bedakan kesalahan provider dari kesalahan input pengguna.** HTTP 5xx dan `ConnectionError` adalah masalah sisi penyedia (pesan: "coba lagi" / "ganti model"), sedangkan transkrip terlalu panjang atau tanpa suara adalah masalah input (pesan: "pilih video lebih pendek"). Melempar pengecualian mentah ke UI membuat pengguna mencoba hal yang salah berulang kali.

---

## 6. Task Plan Terfinalisasi

Estimasi hari bersifat indikatif untuk satu developer penuh waktu.

### Sprint 0 — Fondasi & Prasyarat (3–4 hari)

1. Repo monorepo: `apps/web`, `apps/api`, `apps/worker-light`, `apps/worker-render`, `packages/shared`.
2. ~~`docker-compose.yml` dev: Postgres 16, Redis 7.2, MinIO (emulasi R2), tiga container app. Pin Python 3.12 + FFmpeg 7.~~ — **dibatalkan T9**: jalur lokal langsung di Windows (`.venv-win`, FFmpeg BtbN di `.libs/ffmpeg/`).
3. ~~Set up WSL2 Ubuntu sebagai jalur dev resmi~~ — digantikan keputusan T8 ([`../memory/decisions.md`](../memory/decisions.md)): development lokal = Windows Standalone (`npm run dev`), tanpa WSL/Postgres/Redis.
4. ~~Alembic: seluruh skema §3~~ — dibatalkan T9; skema dibuat/diselaraskan saat startup (`apps/api/app/db/session.py`); tabel `social_accounts`/`scheduled_posts` ada tetapi tidak dipakai sejak D5.
5. Auth: email/password + JWT; middleware otorisasi job-by-owner. *(Historis — satu pengguna lokal tanpa login sejak T9.)*
6. CI: Ruff, mypy, ESLint, `tsc --noEmit`; skeleton pytest + vitest. *(Wujud final: `.github/workflows/ci.yml`, termasuk job Python 3.12 sebagai bukti kompatibilitas.)*
7. Sentry + structured logging (request id, job id) di API dan worker.
8. ~~Daftarkan aplikasi developer TikTok & Meta~~ — dibatalkan oleh D5 (publishing dihapus).
9. ~~Setup Dokploy + Traefik di VPS, termasuk `cpu.max` per container dan volume cache model.~~ — **dibatalkan T9**.

**Definition of Done (history):** ~~`docker compose up` bersih dari error, migrasi jalan, endpoint `/health` hijau, CI lulus, WSL2 terverifikasi bisa menjalankan FFmpeg dengan libass.~~ Digantikan T9: aplikasi lokal jalan lewat `npm run dev`, `/health` hijau, CI lulus.

### Sprint 1 — Ingestion (FR-1.1, FR-1.2) (4–5 hari)

10. API: ~~`POST /uploads/init` → presigned multipart~~ — sejak T9: `POST /uploads/init` → endpoint lokal per potongan (10 MB/part); `GET /uploads/:id/parts` untuk resume; `POST /uploads/complete`; `DELETE` untuk abort.
11. UI: drag-and-drop + progress real-time + auto-retry/resume (IndexedDB menyimpan state `uploadId` + `partNumber`).
12. Validasi berkas di worker (bukan di API): ekstensi, `ffprobe` (codec/durasi/dimensi), batas 3 GB.
13. YouTube ingest: validasi URL (`watch?v=`, `youtu.be/`), metadata via `yt-dlp -J`, cek publik/unlisted + durasi ≤ batas model (konteks model AI ÷ 400 token/menit, maks `MAX_VIDEO_DURATION_MIN`, default 180 menit; semula 60 menit).
14. `ingest` task (pool `ingest` sejak T9): resolve sumber → normalisasi (audio 16 kHz mono WAV untuk STT; mezzanine H.264/AAC) → simpan ke disk lokal.
15. ~~Admission control §4.2~~ — padanan T9: ukuran pool `render`/`stt` di `clipper_shared.dispatcher`.
16. `maintenance` berbasis `expires_at` untuk retensi raw 48 jam — sejak T9 in-process (`clipper_shared.maintenance`), bukan lifecycle rule R2.

**DoD:** upload 2 GB bertahan setelah koneksi diputus di tengah dan dilanjutkan tanpa mengulang dari 0%; URL YouTube privat/durasi di atas batas ditolak dengan pesan jelas.

### Sprint 2 — Transkripsi & AI Scoring (FR-2.1, FR-2.2) (5–7 hari)

17. Interface `Transcriber` (§5.1) + implementasi `FasterWhisperLocal` (`small`, int8). Model di-cache lokal (unduh sekali).
18. `transcribe` task: ekstraksi audio → word-level timestamps → simpan JSON ke SQLite (sejak T9; ~~Postgres + R2~~).
19. Benchmark nyata di host lokal: ukur waktu untuk audio 10/30 menit. **Angka ini yang menggantikan klaim OKR PRD** dan menjadi dasar estimasi "time-to-first-clip" di UI.
20. Prompt Viral Scoring: input transkrip bertsempel waktu → JSON `{segments: [{start, end, score, label, hook_score, completeness, emotional_arc, reason}]}`.
21. Validator + guardrail dasar: 30–60 s per segmen, non-overlap, 1–30 segmen dan maksimal 1 per 3 menit video (`max_clips_for_duration`; semula 1–10), snap ke batas kalimat.
21a. **Pengerasan parsing LLM (§5.4)** — minta `num_clips + 3` lalu saring; parsing toleran-kerusakan dengan `raw_decode()` yang menghitung objek terselamatkan; buang pagar markdown; petakan `reason` → `description`.
21b. **Pertahanan prompt injection (§5.4)** — bersihkan token placeholder (`{transcript}`, `{num_clips}`, …) dan pagar `--- USER DIRECTION START/END ---` dari teks pengguna; batasi 1000 karakter di UI / 2000 di backend; letakkan arahan pengguna **setelah** transkrip.
21c. **Temperature berbasis niat** — 1.0 tanpa arahan, 0.3 bila pengguna mengetik arahan (§5.4).
21d. **Rentang waktu yang diminta pengguna dikecualikan dari saringan durasi minimum**, dicocokkan dengan toleransi ±8 detik (§5.4).
22. Fallback scoring heuristik lokal (energi audio, deteksi tawa, kata kunci) bila LLM gagal atau kuota habis — tanpa ini, satu gangguan API menghentikan seluruh pipeline.
22a. **Klasifikasi kesalahan provider vs input** (§5.4) — HTTP 5xx/`ConnectionError` → "coba lagi / ganti model"; transkrip kosong/terlalu panjang → "pilih video lebih pendek". Jangan lempar pengecualian mentah ke UI.
23. Endpoint `GET /jobs/:id/segments` + `POST /jobs/:id/rescore`.
24. SSE `/jobs/:id/stream` ~~via Redis pub/sub~~ — sejak T9 via `LocalEventBus` in-process (NFR PRD §5).

**DoD:** video 30 menit menghasilkan 1–10 segmen valid dengan skor + label; waktu transkripsi terukur dan terdokumentasi; respons LLM yang rusak sebagian tetap menghasilkan segmen (bukan gagal total), dibuktikan dengan test yang menyuntikkan JSON cacat.

### Sprint 3 — Video Engine & Reframing (FR-3.1, FR-3.2) (6–8 hari)

25. `render` task: potong segmen (stream copy bila keyframe memungkinkan, re-encode bila tidak).
26. **MediaPipe Face Landmarker** (`mediapipe.tasks` vision, `RunningMode.VIDEO`, `num_faces=10`, `min_face_detection_confidence=0.35`). **Wajib `cv2.cvtColor(frame, COLOR_BGR2RGB)` sebelum `detect_for_video()`** (§5.2).
27. Pemilihan subjek per frame: skor = ukuran × (1 + `SPEAKER_BONUS`×bicara) − `CONTINUITY_WEIGHT`×jarak; sinyal bicara dari bukaan mulut 24 lip-landmark dengan `MIN_SPEAK_RATIO=0.18` (§5.2).
28. Penghalusan crop: `EMA_ALPHA=0.15`, deadband `DEADZONE_FRAC=0.02`, snap pada ganti adegan `SNAP_FRAC=0.35`; pertahankan posisi terakhir bila wajah hilang. Lebar crop `min(orig_w, orig_h×1080/1920)` (§5.2).
29. **Pipeline pipe FFmpeg yang aman** — thread pembuangan stderr + watchdog `ENCODE_STALL_TIMEOUT_S=300`; tanpa ini `stdin.write()` akan macet (§5.2).
30. Generator ASS **satu-event-per-kata** (bukan tag `\k`): potongan 4 kata, kata berjalan diwarnai `{\c&H00FFFF&}`; **`PlayResX/Y` dari resolusi hasil probe**, metrik gaya diskalakan dari acuan 1080×1920 (§5.2.1).
31. Burn subtitle + encode: `libx264 -preset fast -crf 18 -pix_fmt yuv420p`; audio diekstrak AAC 192k terpisah lalu di-*mux* `-c:v copy -c:a copy -shortest` (§5.2).
32. Render preview 540×960 `veryfast`/CRF 30 + poster thumbnail → disimpan di disk lokal (sejak T9; ~~unggah R2~~).
33. Kalibrasi reframing pada set uji berlabel → laporkan presisi aktual, jangan klaim 85% sebelum diukur.

**DoD:** klip vertical 9:16 dengan subtitle berjalan terbakar, tepi crop stabil (tidak goyang) pada rekaman 2 pembicara; encode tidak macet pada klip 45 detik; preview ter-render < 60 detik di host lokal (~~vCPU kelas CCX23~~).

### Sprint 4 — Review Studio & Export (FR-4.3 + generator caption FR-4.2) (6–8 hari)

> Tidak ada integrasi sosial (D5: publishing dihapus). Fokus pada pengalaman review, penggerak metrik Export Rate ≥ 65%.

32. UI Review Studio: player vertikal, timeline, daftar segmen + skor/label, quick trim.
33. Editor transkrip: perbaiki kesalahan fonetik → regenerate subtitle tanpa render ulang video penuh.
34. Resolver preset subtitle + preview. **Keputusan:** debounce re-render di server (bukan libass WASM di browser) untuk MVP — lebih sedikit kode, konsisten dengan hasil final. Render preview tetap 540p/veryfast agar terasa responsif.
35. Generator caption + hashtag berbasis AI untuk disalin saat unggah manual (PRD FR-4.2).
36. Export manual: full render on-demand → berkas di `output/<judul>-<id>/` (sejak T9; ~~presigned download URL R2~~); watermark opsional untuk free tier.
37. Status & riwayat job, penanganan error yang bisa dibaca pengguna.

**DoD:** pengguna dapat mengubah transkrip, melihat preview terbarui, dan mengunduh MP4 1080×1920 dengan bitrate optimal (PRD FR-4.3).

### Sprint 5 — Hardening & Closed Beta (5–7 hari)

38. Stress test lokal: 20 upload paralel @ 2 GB, 10 job render bersamaan di host (~~kelas VPS~~). Ukur p95 dan **biaya per video**.
39. Uji starvation CPU: buktikan API tetap responsif selama render berjalan (padanan lokal §4.3).
40. Audit keamanan: ~~TLS 1.3~~, AES-256 at rest, rotasi token, ~~rate limiting~~, validasi SSRF pada URL YouTube.
41. ~~**Restore drill** backup Postgres~~ — dibatalkan T9 (tidak ada PostgreSQL); skema backup lokal `output/` BELUM DITENTUKAN.
42. Validasi metrik ulang memakai kerangka §0.1 — laporkan time-to-first-clip aktual.
43. Observability + alerting (Sentry, dashboard biaya; ~~Flower~~), runbook pribadi.
44. Peluncuran Closed Beta + umpan balik.

---

## 7. Risiko (revisi dari PRD §7)

| Risiko | Dampak | Mitigasi dalam dokumen ini |
|---|---|---|
| **Biaya rendering membengkak** | Operasional tinggi | Preview 540p/veryfast; full render hanya saat export; admission control |
| **Pemblokiran IP YouTube** | Ingest gagal | `yt-dlp` + PO token provider + rotasi proxy residensial |
| **Koneksi putus saat upload > 1 GB** | Pengguna frustrasi | Multipart resume via `ListParts` + IndexedDB |
| **CPU starvation di VPS** (risiko baru akibat D3) — **dibatalkan T9** | API membeku saat render | Padanan lokal: batas pool `RENDER_SLOTS`/`STT_SLOTS` (§4.2) |
| **Backup tidak teruji** (risiko baru akibat D3) — **dibatalkan T9** | Kehilangan data permanen | Butir 41 dihapus; backup lokal BELUM DITENTUKAN |
| **Target OKR kecepatan tidak tercapai** (risiko baru akibat D1) | Ekspektasi produk meleset | §0.1: metrik diganti + diukur di butir 19/42 |

---

## 8. Pertanyaan Terbuka (dibutuhkan sebelum Sprint 0 dimulai)

1. ~~**Spesifikasi VPS Hetzner** — jumlah vCPU/RAM/tipe instance. Ini menentukan `RENDER_SLOTS` dan apakah 1 render + 1 STT paralel aman.~~ **Dibatalkan T9** — tidak ada VPS; padanannya kapasitas host lokal (`constraints.md` §6).
2. **Kelas proxy residensial** — penyedia dan anggaran bulanan untuk ingest YouTube.
3. **Anggaran LLM scoring** — menentukan Gemini Flash vs Haiku sebagai default dan panjang transkrip yang dikirim per permintaan.
4. **Model bisnis / tier** — menentukan perlu tidaknya watermark dan pembatasan kuota di MVP.

---

## 9. Status Dokumen

Dokumen ini **membekukan** rencana untuk D1–D4 (situasi saat ditulis, 2026-09-20). Sejak itu kode sudah ditulis (D4 usang) dan mode deployment dihapus (T9) — lihat catatan di atas dan [`../memory/decisions.md`](../memory/decisions.md).

### 9.1 Sumber Referensi Eksternal

Penyesuaian di §5.2, §5.2.1, §5.4, dan Sprint 2–3 diambil dari studi kode repo referensi:

| Sumber | URL | Bagian yang dipakai |
|---|---|---|
| `jipraks/yt-short-clipper` (v2, Tauri + Python sidecar) | https://github.com/jipraks/yt-short-clipper | `portrait.py` (§5.2), `caption_generator.py` (§5.2.1), `highlight_finder.py` (§5.4), `requirements.txt`, `constants.py` |

**Catatan perbedaan yang disengaja:** proyek referensi adalah aplikasi **desktop** (Tauri, single-user, tanpa server) dan mengambil transkrip dari **subtitle YouTube yang sudah tersedia** — bukan dari Whisper. ClipperAI adalah layanan web multi-pengguna dengan pipeline STT sendiri, sehingga angka dan pola dari repo itu dipakai sebagai **titik awal yang terbukti**, bukan sebagai jawaban akhir. Yang paling langsung dapat dipakai ulang adalah matematika pemilihan subjek (§5.2), format ASS (§5.2.1), dan pengerasan parsing LLM (§5.4).
