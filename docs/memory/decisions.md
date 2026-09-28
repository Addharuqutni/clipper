# Decisions — ADR ClipperAI

Catatan keputusan arsitektur (Architecture Decision Record) yang sudah
**dikunci**. Jangan buka ulang tanpa alasan baru yang kuat — keputusan ini
mengikat seluruh TECH_SPEC.

Format: **ID · Tanggal · Status · Konteks · Keputusan · Konsekuensi**.

Sumber utama: TECH_SPEC §0, §0.1, §1, §2, §3, §4, §5.

---

## D1 — STT Self-Host, CPU-Only

| Field | Isi |
|---|---|
| **ID** | D1 |
| **Tanggal** | 2026-09-20 |
| **Status** | Accepted (dikunci) |
| **Konteks** | PRD §1.3 menetapkan OKR "video 30 menit selesai ≤ 5 menit" dan PRD §6 menawarkan Whisper API atau FastWhisper self-hosted. Keputusan pengguna: jalankan STT sendiri, tanpa GPU. |
| **Keputusan** | STT self-host **CPU-only** memakai `faster-whisper` (CTranslate2, int8). Default MVP = model `small`. |
| **Konsekuensi** | OKR "30 menit → ≤ 5 menit" **TIDAK TERCAPAI** dan wajib diganti. Metrik pengganti: **time-to-first-clip ≤ 25 menit**; end-to-end penuh ~50–90 menit. Diperkenalkan abstraksi `Transcriber` (§5.1) sebagai satu-satunya hedge agar D1 dapat dibalik tanpa penulisan ulang. |

Sumber: TECH_SPEC §0 (D1), §0.1, §5.1.

---

## D2 — Publishing Ditunda, MVP Download-Only

| Field | Isi |
|---|---|
| **ID** | D2 |
| **Tanggal** | 2026-09-20 |
| **Status** | Accepted (dikunci) |
| **Konteks** | Risiko #1 PRD §7: proses app review Meta/TikTok lambat dan tidak dapat dikendalikan. Jalur kritis rilis tidak boleh bergantung padanya. |
| **Keputusan** | Integrasi publishing **ditunda**. MVP bersifat **download-only**. Aplikasi developer TikTok/Meta tetap didaftarkan sejak **Sprint 0 butir 8**. |
| **Konsekuensi** | Sprint 4 menyusut; OAuth keluar dari jalur kritis (FR-4.1 ditunda). Waktu yang tersisa dialihkan ke Review Studio — penggerak metrik "Export & Publish Rate ≥ 65%". Tabel `social_accounts` dan `scheduled_posts` **tetap dibuat** di Sprint 0 meski belum aktif. FR-4.2 sebagian tetap rilis: generator caption + hashtag (tidak butuh OAuth). |

Sumber: TECH_SPEC §0 (D2), §3, §6 Sprint 4 (butir 32–37).

---

## D3 — VPS Self-Managed (Hetzner + Dokploy)

| Field | Isi |
|---|---|
| **ID** | D3 |
| **Tanggal** | 2026-09-20 |
| **Status** | **SUPERSEDED oleh T9** (2026-09-27) |
| **Konteks** | PRD §5 NFR mengasumsikan worker render dapat *auto-scale* berbasis beban antrean — asumsi platform cloud terkelola. |
| **Keputusan** | Deploy di **VPS self-managed**: Hetzner + Dokploy. Tidak ada auto-scale platform. **vCPU dedicated (CCX) wajib**, bukan shared (CX). |
| **Konsekuensi** | Auto-scale berbasis beban antrean menjadi utang teknis (ditinjau saat migrasi ke cloud/GPU). Wajib: admission control (§4.2), QoS/isolasi CPU (§4.3), backup teruji + restore drill (§4.4). Risiko baru: CPU starvation dan backup tak teruji. FFmpeg di shared vCPU menghasilkan waktu render tak terprediksi. |

Sumber: TECH_SPEC §0 (D3), §4, §5.3, §7.

---

## D4 — Hanya Dokumen, Tanpa Scaffold Kode

| Field | Isi |
|---|---|
| **ID** | D4 |
| **Tanggal** | 2026-09-20 |
| **Status** | **SUPERSEDED** — tidak lagi berlaku |
| **Konteks** | Deliverable fase perencanaan adalah dokumen yang dibekukan, bukan kode. |
| **Keputusan** | TECH_SPEC adalah **deliverable**. Tidak ada scaffold kode. |
| **Konsekuensi** | Baris "Scaffold" di Sprint 0 adalah **tugas**, bukan pekerjaan yang sudah dilakukan. Semua FR berstatus belum diimplementasikan. **Namun sekarang usang:** |
| **Superseded oleh** | Instruksi pengguna berikutnya: *"Lanjutkan proses development secara keseluruhan."* Sejak itu **kode boleh dan memang sedang ditulis** (agen paralel mengerjakan `apps/`, `packages/`, `docker-compose.yml`, CI). D4 **tidak boleh** dipakai untuk menolak pekerjaan implementasi. |
| **Yang masih berlaku** | Fakta historis bahwa saat TECH_SPEC ditulis belum ada kode, dan status "belum diimplementasikan" pada FR adalah benar **pada saat itu**. |

Sumber: TECH_SPEC §0 (D4), §9; supersede dari instruksi pengguna lanjutan.

---

## D5 — Fitur Publishing Dihapus

| Field | Isi |
|---|---|
| **ID** | D5 |
| **Tanggal** | 2026-09-26 |
| **Status** | Accepted |
| **Konteks** | Auth TikTok (FR-4.1) sempat diimplementasikan, tetapi butuh pendaftaran app di TikTok Developer Portal (client key/secret) yang tidak diinginkan pengguna. |
| **Keputusan** | Seluruh fitur publishing **dihapus**: router `/publishing`, OAuth TikTok, registry `clipper_shared.publishing`, halaman `/publishing`, dan link navbar. Produk murni download-only. |
| **Konsekuensi** | FR-4.1 dan FR-4.2 (bagian posting) keluar dari scope. Tabel `social_accounts` dan `scheduled_posts` **dibiarkan** (kosong, tanpa kode pemakai) agar tidak perlu migrasi drop. `TokenCipher` tetap dipakai untuk API key penyedia AI. Menghidupkan kembali publishing = keputusan baru. |

Sumber: instruksi pengguna 2026-09-26; menggantikan D2.

---

# Keputusan Turunan

Keputusan-keputusan berikut tercatat di TECH_SPEC sebagai konsekuensi atau
pilihan implementasi eksplisit.

## T1 — FastAPI Saja (bukan Node.js)

| Field | Isi |
|---|---|
| **Status** | Accepted |
| **Konteks** | PRD §6 menawarkan "FastAPI (Python) **atau** Node.js" untuk backend API. |
| **Keputusan** | **FastAPI saja.** Node.js tidak dipakai untuk backend. |
| **Konsekuensi** | Satu bahasa untuk seluruh pipeline AI. Node hanya muncul sebagai runtime frontend (Next.js). Pydantic v2 + SQLAlchemy 2.0 async sebagai fondasi API. |

Sumber: TECH_SPEC §2.

---

## T2 — Celery (bukan BullMQ)

| Field | Isi |
|---|---|
| **Status** | **SUPERSEDED oleh T9** (2026-09-27) |
| **Konteks** | PRD §6 menawarkan "Celery / BullMQ + Redis". |
| **Keputusan** | **Celery 5** + Redis 7 sebagai broker/backend. |
| **Konsekuensi** | Worker berat (FFmpeg + model ML) adalah proses Python native, tanpa shell-out lintas bahasa. Queue terpisah: `ingest`, `transcribe`, `analyze`, `render`, `maintenance`. Flower untuk monitoring. |

Sumber: TECH_SPEC §2.

---

## T3 — Pin Python 3.12 (Image Container)

| Field | Isi |
|---|---|
| **Status** | **SUPERSEDED oleh T9** — Python 3.12–3.14 didukung setara |
| **Konteks** | Tidak ada Python ≤ 3.12 di environment ini: Windows **3.14.6**, WSL **3.14.4** (hanya `/usr/bin/python3.14`). Saat keputusan dibuat, ekosistem ML (`faster-whisper`, MediaPipe, OpenCV) dianggap belum matang di 3.14. |
| **Keputusan** | **Pin Python 3.12.x** di semua image container produksi (base `python:3.12-slim-bookworm`). |
| **Konsekuensi** | Pin 3.12 berlaku untuk **image Docker/VPS**. Sejak T8, host Windows menjalankan jalur lokal dengan **Python 3.14 di `.venv-win`** (mediapipe, ctranslate2/faster-whisper, dan opencv punya wheel `cp314`); hasil suite di 3.14 **bukan** bukti kompatibilitas produksi (`make test-312`). Lihat `constraints.md` §2. |

Sumber: TECH_SPEC §6 Sprint 0 butir 2; probe host; T8.

---

## T4 — WSL2/Docker sebagai Jalur Dev Resmi

| Field | Isi |
|---|---|
| **Status** | **SUPERSEDED oleh T8** (2026-09-26) |
| **Konteks** | Host pengembangan adalah Windows; pipeline butuh FFmpeg dengan libx264 + libass dan toolchain Linux. **Docker tidak terpasang** di Windows maupun WSL (`systemctl is-active docker` = inactive). |
| **Keputusan** | **WSL2 Ubuntu** adalah jalur dev resmi. Host Windows **tidak dipakai** untuk menjalankan pipeline. |
| **Koreksi (2026-09-20)** | Jalur **"WSL2 + Docker Compose" TIDAK LAYAK saat ini** karena Docker absen. Jalur yang layak adalah **WSL2 native** (Postgres/Redis/MinIO sebagai proses langsung, atau instance ter-manage) sampai Docker dipasang. `docker-compose.yml` tetap jadi **target**, bukan sesuatu yang bisa dieksekusi sekarang. |
| **Konsekuensi** | FFmpeg **>= 7** (libx264 + libass) **terverifikasi ada** di WSL2 (8.0.1). WSL2 hanya **1.9 GiB RAM** → stack penuh **tidak muat**; dev lokal wajib **profil tereduksi**. Lihat `constraints.md` §4–§5. |

Sumber: TECH_SPEC §6 Sprint 0 butir 2, 3, 9; probe host (`constraints.md` §4–5).

---

## T8 — Windows Standalone sebagai Jalur Lokal Resmi

| Field | Isi |
|---|---|
| **ID** | T8 |
| **Tanggal** | 2026-09-26 |
| **Status** | Accepted — menggantikan T4 |
| **Konteks** | Pengguna meminta aplikasi "easy use", satu perintah, satu jendela, dan **tanpa Redis/PostgreSQL**. Jalur WSL butuh Postgres + Redis + dua worker Celery + banyak terminal; mode Standalone (SQLite + runner in-process) sudah ada di kode. |
| **Keputusan** | Jalur lokal **selalu Standalone** di Windows native: `scripts/_env.cmd` memaksa `STANDALONE=true`. SQLite `output/clipper.db` (aiosqlite), `ThreadPoolExecutor` in-process (`clipper_shared.dispatcher`), `LocalEventBus` untuk SSE, storage lokal di `output/`. Satu perintah: `npm run dev` (`scripts/dev.mjs`) atau klik dua kali `start.cmd`; setup otomatis lewat `scripts/setup.cmd` (`.env` + kunci acak, `.venv-win` + `pip install -e .[api,worker-light,worker-render]`, FFmpeg BtbN ke `.libs/ffmpeg/`, `face_landmarker.task` ke `.models/`, `npm ci`). |
| **Konsekuensi** | PostgreSQL 16, Redis 7, Celery worker, dan `docker-compose.yml` **hanya** untuk deployment Docker/VPS (D3). Tooling WSL dihapus (`run_worker_wsl.sh`, `setup_db_wsl.sh`, `run_local.py`, `inspect_queue.py`, `run-worker.cmd`, `start-all.cmd`, `clean_test_data.py`, `repair_transcripts.py`, `.venv-worker/`). Data Postgres lama dimigrasikan ke SQLite. Di Windows, `uvicorn --reload` mengirim `CTRL_C_EVENT` ke seluruh konsol, jadi `dev.mjs` memberi tiap layanan konsol tersembunyi sendiri. Test API diarahkan ke direktori sementara (`apps/api/tests/conftest.py`) agar tidak menulis ke basis data pengembang. |

Sumber: instruksi pengguna 2026-09-26 ("easy use", "satu perintah", "tidak membuka banyak terminal", "jangan gunakan redis/postgresql").

---

## T9 — Lokal Saja: Docker dan Mode Distributed Dihapus

| Field | Isi |
|---|---|
| **ID** | T9 |
| **Tanggal** | 2026-09-27 |
| **Status** | Accepted — menggantikan D3, T2, T3 |
| **Konteks** | Pengguna tidak ingin memakai Docker. Mode distributed (PostgreSQL + Redis + Celery + S3/MinIO) hanya ada untuk deployment Docker/VPS dan belum pernah dijalankan; review menemukan >10 blocker di jalur itu, sementara kode dua-mode menggandakan setiap jalur (storage, event, slot, DB). |
| **Keputusan** | Aplikasi murni lokal: satu proses FastAPI di `127.0.0.1` menjalankan pipeline di thread pool per tahap (`clipper_shared.dispatcher`; ukuran pool = batas paralel, menggantikan semaphore Redis), SQLite, dan disk (`clipper_shared.storage`). Dihapus: `docker-compose.yml`, Dockerfile, Alembic, Celery app, `slots.py`, `queue_names.py`, S3/boto3, Makefile, penjaga versi 3.12. Unggahan memakai endpoint lokal per potongan dengan resume. Saat startup job yatim ditandai gagal. |
| **Konsekuensi** | Tidak ada auto-scale atau deployment server; memakai di VPS berarti menulis ulang lapisan antrean/penyimpanan. Pembatalan job berhenti di titik periksa (`emit`), tidak memutus FFmpeg/Whisper yang sedang berjalan. `.env` dimuat python-dotenv, bukan batch. |

Sumber: instruksi pengguna 2026-09-27 ("saya tidak ingin menggunakan docker"; pilihan "Hapus Docker + mode distributed").

---

## T5 — AES-256-GCM untuk Token (bukan Fernet Polos)

| Field | Isi |
|---|---|
| **Status** | Accepted |
| **Konteks** | PRD §5 mewajibkan token OAuth disimpan terenkripsi "sesuai standar keamanan platform Meta dan TikTok". |
| **Keputusan** | `social_accounts.encrypted_token` memakai **AES-256-GCM** via `cryptography`, key dari env/KMS — **bukan** Fernet tanpa AAD. |
| **Konsekuensi** | Rotasi key direncanakan masuk Sprint 5 (butir 40: audit keamanan). Memenuhi NFR PRD §5. |

Sumber: TECH_SPEC §3, §6 Sprint 5 butir 40.

---

## T6 — Maintenance Task untuk Retensi 48 Jam (bukan Lifecycle Rule Saja)

| Field | Isi |
|---|---|
| **Status** | Accepted — diimplementasikan in-process oleh T9 (`clipper_shared.maintenance`), `expires_at` dihitung dari render terakhir |
| **Konteks** | PRD §5 meminta raw media dihapus **48 jam setelah proses pemotongan selesai** — ini bergantung **kejadian**, sedangkan lifecycle rule R2 berbasis **prefix + umur objek**. Keduanya tidak ekuivalen. |
| **Keputusan** | Implementasi retensi via **Celery beat `maintenance` task** yang menghapus berdasarkan `source_media.expires_at` di DB. Lifecycle rule R2 hanya **jaring pengaman** (mis. hapus paksa di 7 hari). |
| **Konsekuensi** | Butuh kolom `expires_at` di `source_media` (sudah ada di §3) dan task `maintenance` di queue terpisah. Dikerjakan Sprint 1 butir 16. |

Sumber: TECH_SPEC §3 (Lifecycle R2), §6 Sprint 1 butir 16.

---

## T7 — Debounce Re-Render Server untuk Preview Subtitle

| Field | Isi |
|---|---|
| **Status** | Accepted — worker kini thread pool in-process (T9), bukan Celery |
| **Konteks** | Saat pengguna menyunting transkrip atau preset subtitle, perlu keputusan di mana preview di-render ulang: server atau browser. |
| **Keputusan** | **Debounce re-render di server** (Celery), **bukan** libass WASM di browser. Preview tetap 540p/`veryfast` agar terasa responsif. |
| **Konsekuensi** | Lebih sedikit kode dan hasil konsisten dengan render final. Bergantung pada ketersediaan slot render di `worker-render` — karena itu `RENDER_SLOTS=1` harus cukup untuk pengalaman ini (lihat `constraints.md`). |

Sumber: TECH_SPEC §6 Sprint 4 butir 34.

---

## Ringkasan Cepat

| ID | Keputusan | Sumber |
|---|---|---|
| D1 | STT self-host CPU-only (`faster-whisper`, default `small`) | §0, §0.1, §5.1 |
| D2 | Publishing ditunda; MVP download-only | §0, §6 Sprint 4 |
| D3 | ~~VPS self-managed (Hetzner + Dokploy)~~ → **SUPERSEDED** oleh T9 | §0, §4 |
| D4 | ~~Hanya dokumen, tanpa scaffold kode~~ → **SUPERSEDED**, kode sedang ditulis | §0, §9 |
| T1 | FastAPI saja (bukan Node.js) | §2 |
| T2 | ~~Celery~~ → **SUPERSEDED** oleh T9 (thread pool in-process) | §2 |
| T3 | ~~Pin Python 3.12~~ → **SUPERSEDED** oleh T9 (3.12–3.14) | §6 Sprint 0 |
| T4 | ~~WSL2 native = jalur dev resmi~~ → **SUPERSEDED** oleh T8 | §6 Sprint 0 |
| T5 | AES-256-GCM untuk token (bukan Fernet polos) | §3, §6 Sprint 5 |
| T6 | Maintenance task untuk retensi 48 jam | §3, §6 Sprint 1 |
| T7 | Debounce re-render server untuk preview subtitle | §6 Sprint 4 |
| T8 | Lokal = Windows Standalone (SQLite, tanpa Postgres/Redis/Celery), `npm run dev` | instruksi pengguna 2026-09-26 |
| T9 | Lokal saja: Docker, Celery, Redis, Postgres, S3, Alembic dihapus | instruksi pengguna 2026-09-27 |

---

## BELUM DITENTUKAN

- **BELUM DITENTUKAN** — Skema key management (env vs KMS mana) untuk T5.
- **BELUM DITENTUKAN** — Kelas proxy residensial dan anggaran LLM (belum ada
  keputusan tertutup).

Sumber: TECH_SPEC §3, §4.1, §8.
