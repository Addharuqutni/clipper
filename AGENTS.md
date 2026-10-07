# AGENTS.md

## Bahasa dan scope

- Tulis kesimpulan, ringkasan, laporan akhir, dan pertanyaan kepada pengguna dalam Bahasa Indonesia.
- Ikuti konvensi repo untuk kode, nama simbol, pesan error, dan isi file teknis.
- Perubahan yang dibuat pengguna atau agen lain adalah milik pengguna. Jangan menimpa atau membuangnya tanpa instruksi eksplisit.

## Model proyek

ClipperAI adalah monorepo aplikasi lokal Windows native untuk memotong video panjang menjadi klip vertikal 9:16:

- `apps/web/` — frontend Next.js (App Router, Turbopack, Tailwind CSS, Vitest).
- `apps/api/` — API FastAPI satu proses (Pydantic v2, SQLAlchemy 2.0 async, aiosqlite, SSE).
- `apps/worker-light/` — tugas ingest media, parsing playlist, YouTube live/DVR clamping (`media_fetcher`), STT scoring client, dan social caption/hashtag generator.
- `apps/worker-render/` — pipeline rendering video (reframer, face tracking MediaPipe, overlay compositor, FFmpeg pipe, dan evaluasi reframe).
- `packages/shared/src/clipper_shared/` — pustaka Python bersama:
  - `dispatcher` — scheduler pool worker in-process per tahap (`RENDER_SLOTS=1`, `STT_SLOTS=1`, `INGEST_WORKERS=2`).
  - `db` — sesi dan manajemen engine basis data SQLite lokal.
  - `storage`, `job_media`, `job_state` — manajemen penyimpanan media lokal per-job (`output/<slug-judul>-<8char-id>/`), kompatibilitas key warisan (`is_legacy_key`), dan helper transisi status job.
  - `transcript` — kanonikal `TranscriptWord`, normalisasi Whisper vs YouTube, update kata atomik (`update_word_in_list`) dengan verifikasi `expected_text`.
  - `stt` — abstraksi kontrak `Transcriber` dan runner `faster-whisper`.
  - `ai_provider` — klien LLM (Gemini 2.5 Flash default, fallback Claude Haiku, lalu heuristik lokal).
  - `youtube_cookies` — validasi dan injeksi cookies Netscape untuk yt-dlp.
  - `security` — enkripsi AES-256-GCM (`TokenCipher`) via `cryptography`.
  - `maintenance` — pembersihan raw media kedaluwarsa (`purge_expired_raw_media`) dan penyisiran workspace.
  - `processes` — pelacakan proses anak (FFmpeg / yt-dlp) untuk pembatalan aman (`terminate_job`).
  - `reframe/` & `subtitles/` — logika tracking wajah, mode reframe (wajah tunggal/ganda/aktif), parser ASS, dan YouTube subtitle.
- `scripts/` — launcher Windows, setup otomatis, migrasi layout output (`migrate_output_layout.py`), dan evaluasi reframe (`eval_reframe.py`).
- `output/` — SQLite (`clipper.db`) dan direktori folder media per-job; `.work/`, `.models/`, `.libs/ffmpeg/`, `.venv-win/`, dan `node_modules/` adalah artefak lokal yang diabaikan git. Kolom basis data `r2_key` adalah penamaan warisan yang menampung jalur berkas lokal di bawah `output/`.

Jalur resmi development adalah **Windows Standalone (T8/T9)**: satu proses API menjalankan pipeline in-process dengan SQLite dan storage lokal. Jangan pernah mengasumsikan PostgreSQL, Redis, Celery, Docker, WSL, S3/R2, atau Alembic tersedia untuk development lokal.

## Perintah resmi

Dari root repo:

```cmd
npm run dev
start.cmd
stop.cmd
```

- `npm run dev` menjalankan setup idempoten bila diperlukan, lalu meluncurkan API di port `8000` dan frontend web di port `3000` dalam satu terminal (`scripts/dev.mjs`).
- `start.cmd` menjalankan `node scripts\dev.mjs --open` dalam jendela tersendiri serta membuka browser secara otomatis ke `http://localhost:3000`.
- `stop.cmd` menghentikan proses API/web/child process milik repo ini yang tertinggal dan membebaskan port 8000/3000 tanpa mematikan proses asing.
- `npm run dev:web` menjalankan frontend Next.js saja.
- `scripts\setup.cmd` hanya menjalankan setup idempoten (lingkungan `.env`, dependensi Python `.venv-win`, build FFmpeg BtbN ke `.libs\ffmpeg\`, model wajah ke `.models\`, dan `npm ci`).
- `scripts\migrate_output_layout.py` utilitas migrasi layout file lama ke struktur satu folder per job (bawaan dry-run; gunakan `--apply` untuk eksekusi).

API listen hanya di `127.0.0.1:8000` (`run-api.cmd` uvicorn tanpa `--reload`). Endpoint health check:

```cmd
curl http://localhost:8000/health
curl http://localhost:8000/health/ready
```

- `GET /health` — liveness check tanpa koneksi database (`{"status":"ok",...}`).
- `GET /health/ready` — readiness check dengan verifikasi database SQLite (`{"status":"ready","checks":{"database":true}}`).

## Pemeriksaan perubahan

Pilih pemeriksaan sesuai scope perubahan. Jalankan dari shell bersih, bukan dari proses dev yang sedang berjalan.

Frontend:

```cmd
npm run lint
npm run typecheck
npm test
npm run build
```

Python:

```cmd
.venv-win\Scripts\python.exe -m pytest
.venv-win\Scripts\python.exe -m ruff check apps packages
.venv-win\Scripts\python.exe -m mypy apps/api/app packages/shared/src apps/worker-light/worker_light apps/worker-render/worker_render
```

- Test Python memakai basis data dan direktori storage sementara via `conftest.py`; jangan pernah mengarahkan test ke `output\clipper.db` nyata.
- Direktori test Python sengaja tanpa `__init__.py` agar tidak tumpang tindih; nama file test wajib unik di seluruh repo.
- Perubahan frontend wajib minimal melewati test yang relevan dan `npm run typecheck`; perubahan UI atau build-critical wajib lolos `npm run build`.
- Sebelum menyatakan pekerjaan selesai, jalankan smoke check pada jalur yang diubah, bukan hanya test unit.

## Keputusan terkunci & batasan operasional

Keputusan arsitektur yang mengikat (sumber: [`docs/memory/decisions.md`](docs/memory/decisions.md) & [`docs/memory/constraints.md`](docs/memory/constraints.md)):

1. **Jalur lokal murni Standalone (T8, T9)**: Docker, Celery, Redis, PostgreSQL, Alembic, dan S3 telah dihapus. Jangan pernah menyarankan atau mengembalikan dependensi tersebut.
2. **STT CPU-Only & Metrik Kecepatan (D1, F6)**: Transkripsi berjalan di CPU via `faster-whisper` (int8). Target awal PRD (video 30 mnt selesai ≤5 mnt) **gagal/tidak tercapai**. Metrik pengganti resmi: **time-to-first-clip ≤ 25 menit**. Dilarang mengklaim OKR kecepatan PRD tercapai.
3. **Pekerjaan berat wajib lewat Dispatcher (F2, F5)**: Semua task FFmpeg, STT, dan render wajib dieksekusi melalui `clipper_shared.dispatcher`. Batas paralel bawaan: `RENDER_SLOTS=1`, `STT_SLOTS=1`, `INGEST_WORKERS=2`. Menjalankan proses di luar pool akan membekukan API. Dilarang menaikkan nilai slots tanpa pengukuran kapasitas terverifikasi (`metrics.md`).
4. **Retensi 48 Jam In-Process (F3, T6)**: Pembersihan raw media kedaluwarsa dijalankan secara berkala oleh API melalui `clipper_shared.maintenance.purge_expired_raw_media` berbasis `source_media.expires_at`. Jangan mengandalkan lifecycle OS atau storage.
5. **FFmpeg wajib libx264 dan libass**: Memerlukan FFmpeg ≥ 7 dengan dukungan `libx264` dan `libass` via `FFMPEG_BINARY` di `.libs/ffmpeg/` (build BtbN), bukan build sembarang dari `PATH`. Verifikasi via `ffmpeg -version | grep -E 'libx264|libass'`.
6. **Token & Kunci Rahasia (T5)**: API key AI provider dan cookies YouTube disimpan terenkripsi AES-256-GCM via pustaka `cryptography` menggunakan `TOKEN_ENCRYPTION_KEY`.
7. **Preview Subtitle Server Debounce (T7)**: Preview subtitle di editor adalah debounce re-render server (540p / `veryfast`) melalui pool render dispatcher, bukan libass WASM browser.
8. **Fitur Publishing Dihapus (D5)**: Aplikasi murni download-only. Router `/publishing`, OAuth TikTok/Meta, dan halaman publishing telah dihapus. Jangan mengimplementasikan ulang alur publishing tanpa ADR baru; tabel `social_accounts` dan `scheduled_posts` dibiarkan kosong tanpa kode pemakai.
9. **Diarization Ditunda & AI Scoring Fallback**: Diarization via `pyannote` ditunda karena batasan CPU (digantikan heuristik energi + jeda). Penilaian klip menggunakan Gemini 2.5 Flash sebagai default dengan fallback Claude Haiku lalu heuristik lokal. `POST /jobs` mengembalikan HTTP 422 jika provider AI belum siap.
10. **Hal Belum Ditentukan**: Nilai final slot concurrency, proxy residensial & anggaran LLM komersial, ketersediaan GPU, dan skema backup `output/` belum diputuskan secara resmi. Jangan berasumsi sudah ada keputusan final.

## Aturan pengembangan

### Domain dan keputusan

- Dokumen keputusan dan batasan aktual berada di [`docs/memory/decisions.md`](docs/memory/decisions.md) dan [`docs/memory/constraints.md`](docs/memory/constraints.md). File [`docs/agents/domain.md`](docs/agents/domain.md) adalah rujukan umum format domain. File seperti `CONTEXT.md`, `CONTEXT-MAP.md`, dan `docs/adr/` saat ini tidak ada di repo — lanjutkan pekerjaan tanpa membuatnya hanya untuk memenuhi formalitas aturan.
- Gunakan istilah domain yang sudah dipakai kode dan dokumentasi resmi. Jika suatu usulan bertentangan dengan keputusan terdokumentasi (ADR), nyatakan konflik tersebut secara eksplisit.

### Issue tracker lokal

Issue tidak memakai remote tracker. Saat diminta membuat atau menerbitkan issue:

- Simpan di `.scratch/<feature-slug>/` (folder `.scratch/` dibuat secara on-demand jika dibutuhkan).
- Spesifikasi ada di `spec.md`.
- Ticket implementasi satu file per isu di `issues/<NN>-<slug>.md`, mulai dari `01`.
- Letakkan `Status:` dekat bagian atas; gunakan label dari [`docs/agents/triage-labels.md`](docs/agents/triage-labels.md).
- Komentar ditambahkan di bawah `## Comments`.
- Ikuti detail operasi di [`docs/agents/issue-tracker.md`](docs/agents/issue-tracker.md).

### Keamanan dan data lokal

- API tidak memiliki login dan hanya aman untuk akses lokal; pertahankan binding `127.0.0.1` dan validasi `Host`.
- API key provider AI dan cookies YouTube harus tetap terenkripsi dengan `TOKEN_ENCRYPTION_KEY`; jangan pernah mengembalikan API key tersimpan ke UI atau log.
- Pertahankan validasi SSRF untuk endpoint AI dan URL subtitle/provider. Jangan menambahkan bypass host lokal tanpa kebutuhan eksplisit.
- Jangan menghapus `output/clipper.db`, media, credential, atau file kerja pengguna sebagai bagian dari test atau cleanup tanpa instruksi eksplisit.
- Perubahan render harus mempertahankan seek frame-accurate saat subtitle digunakan, output canonical yang dirujuk database, dan output klip lokal yang diharapkan.

### Windows dan line ending

- File `.cmd` wajib CRLF; file `.sh`, Python, TypeScript, JSON, YAML, Markdown, dan TOML mengikuti aturan LF di `.gitattributes`.
- Launcher memakai konsol tersembunyi terpisah (`windowsHide: true`) dan menghentikan child process dengan `taskkill /T /F /PID`; pembatalan job memutus rantai proses anak (`clipper_shared.processes.terminate_job`). Jangan mengganti pola ini tanpa verifikasi langsung di Windows.

## Rujukan utama

- Panduan Pengguna:
  - [`docs/panduan/mulai-cepat.md`](docs/panduan/mulai-cepat.md) — langkah pertama setup dan menjalankan aplikasi.
  - [`docs/panduan/pemakaian.md`](docs/panduan/pemakaian.md) — alur input media, transkripsi, AI scoring, render, dan hasil klip.
  - [`docs/panduan/pemecahan-masalah.md`](docs/panduan/pemecahan-masalah.md) — solusi masalah umum runtime Windows dan dependensi.
- Panduan Pengembangan:
  - [`docs/pengembangan/arsitektur.md`](docs/pengembangan/arsitektur.md) — arsitektur pipeline in-process dan struktur komponen.
  - [`docs/pengembangan/konfigurasi.md`](docs/pengembangan/konfigurasi.md) — referensi environment variables dan batas runtime.
  - [`docs/pengembangan/perintah.md`](docs/pengembangan/perintah.md) — detail perintah pengujian, linting, typecheck, dan health check.
  - [`docs/pengembangan/instalasi-manual.md`](docs/pengembangan/instalasi-manual.md) — pemasangan manual dependensi bila setup otomatis bermasalah.
  - [`docs/pengembangan/windows.md`](docs/pengembangan/windows.md) — penanganan proses latar dan jebakan spesifik Windows.
- Catatan Keputusan & Batasan Sistem:
  - [`docs/memory/decisions.md`](docs/memory/decisions.md) — Architecture Decision Records (ADR) resmi ClipperAI.
  - [`docs/memory/constraints.md`](docs/memory/constraints.md) — batasan operasional lingkungan, hardware, dan daftar larangan (FORBIDDEN).
  - [`docs/memory/architecture.md`](docs/memory/architecture.md) — catatan arsitektur mendalam.
  - [`docs/memory/metrics.md`](docs/memory/metrics.md) — metrik terukur sistem dan hasil benchmark performa.
  - [`docs/memory/technical-debt.md`](docs/memory/technical-debt.md) — catatan utang teknis dan area yang ditunda.
- Spesifikasi & Aturan Agen:
  - [`docs/spesifikasi/PRD.md`](docs/spesifikasi/PRD.md) & [`docs/spesifikasi/TECH_SPEC.md`](docs/spesifikasi/TECH_SPEC.md) — spesifikasi produk dan teknis.
  - [`docs/agents/issue-tracker.md`](docs/agents/issue-tracker.md) & [`docs/agents/triage-labels.md`](docs/agents/triage-labels.md) — format dan label tiket lokal.
  - [`docs/agents/domain.md`](docs/agents/domain.md) — pedoman dokumentasi domain.
