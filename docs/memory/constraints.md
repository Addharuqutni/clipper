# Constraints — Batasan Operasional & Lingkungan

> **Catatan 2026-09-27 (T9):** aplikasi berjalan **lokal saja** di Windows
> (satu proses, Python 3.12–3.14). Docker, WSL, PostgreSQL, Redis, Celery, R2,
> dan pin Python 3.12 untuk container **dibatalkan**; bagian yang hanya berlaku
> untuk deployment VPS sudah dihapus dan diganti satu bagian riwayat di §9.

File ini adalah **filter pertama** sebelum menjalankan apa pun secara lokal.
Banyak perintah yang "wajar" di laptop lain akan **salah** di environment ini.

Sumber: TECH_SPEC §4, §6 (Sprint 0), §2; T9.

---

## 1. Hasil Probe Host — TERVERIFIKASI

> **Koreksi terhadap TECH_SPEC §1.1.** Data di bawah ini adalah hasil probe
> langsung dan **lebih akurat** daripada asumsi di TECH_SPEC §1.1. Bila
> keduanya berbenturan, **bagian ini yang menang**.

### 1.1 Windows host (jalur eksekusi lokal — T8)

| Item | Status terverifikasi | Implikasi |
|---|---|---|
| OS host | **Windows 10 (19045)** | Jalur lokal resmi: **Standalone native** (`npm run dev` / `start.cmd`) |
| Python host | **3.14.6** di `.venv-win` | Cukup untuk API + worker in-process (wheel `cp314` tersedia); 3.12–3.14 didukung setara (T9) |
| Node host | **v24.13.1** | Target 22 LTS; npm memberi `EBADENGINE` tetapi `next dev` jalan |
| `nvidia-smi` | tidak ada | Tidak ada GPU → konsisten dengan D1 (CPU-only) |
| `ffmpeg` | `.libs/ffmpeg/` (build BtbN, diunduh `setup.cmd`) | `libass` + `libx264` terverifikasi; dipakai lewat `FFMPEG_BINARY`, bukan `PATH` |
| PostgreSQL / Redis | **tidak dipakai** lokal | SQLite `output/clipper.db` + runner in-process + `LocalEventBus` |
| `docker` | tidak ada | Tidak dipakai sama sekali — mode distributed dihapus (T9) |

Sumber: probe host langsung (verifikasi pengguna, 2026-09-26), menggantikan TECH_SPEC §1.1.

---

## 2. Python — 3.12–3.14, Tanpa Container

- Aplikasi menerima Python **3.12–3.14** (`pyproject.toml`: `>=3.12,<3.15`).
- **Lokal Windows** memakai Python **3.14.6** di `.venv-win`
  (`mediapipe`, `ctranslate2`/`faster-whisper`, dan
  `opencv-python-headless` menyediakan wheel `cp314`).
- Tidak ada image container produksi (T9). **Bukti kompatibilitas 3.12 adalah
  job CI** (`.github/workflows/ci.yml`, `python-version: "3.12"`), bukan
  `make test-312` — Makefile sudah dihapus.

Sumber: TECH_SPEC §6 Sprint 0 butir 2; CI workflow; status instalasi dari probe host.

---

## 3. FFmpeg >= 7 — Wajib libx264 DAN libass

**Koreksi pin:** bukan lagi "FFmpeg 7.x" persis, melainkan
**FFmpeg >= 7 dengan libx264 DAN libass**.

| Lokasi | Status verifikasi |
|---|---|
| Lokal Windows `.libs/ffmpeg/` | ✅ build BtbN `N-126755+`, `--enable-libass` + `--enable-libx264` |

> ⚠️ Banyak paket FFmpeg distro **tidak** menyertakan `libass` — jangan
> berasumsi build lain otomatis aman, verifikasi per build.

> Build BtbN Windows memakai TLS **Schannel** (`--enable-schannel`). Aman untuk
> alur sekarang (yt-dlp mengunduh sendiri, FFmpeg hanya menggabungkan lokal).
> Bila kelak memakai `--download-sections` (FFmpeg mengunduh rentang HTTPS),
> ganti ke build gyan.dev GnuTLS — Schannel dilaporkan menggantung di sana.

Verifikasi cepat (harus memunculkan keduanya):

```
ffmpeg -version | grep -E 'libx264|libass'
```

Sumber: TECH_SPEC §1.1, §2, §6 Sprint 3 butir 28–29; probe host.

---

## 4. Tanpa Docker & Tanpa WSL

- Lokal **tidak** memakai Docker maupun WSL (T8). Tidak ada alasan memasang
  Docker di workstation untuk development — dan tidak ada jalur deployment
  yang membutuhkannya (T9).
- Tidak ada `docker-compose.yml`, Dockerfile, `Makefile`, maupun
  `scripts/validate_compose.py` di repo ini. Jangan menulis instruksi yang
  mengasumsikannya (mis. `make test-312`, `docker compose up`).

Sumber: probe host; TECH_SPEC §1.1; T8; T9.

---

## 5. Kapasitas Lokal

| Beban | Catatan |
|---|---|
| API + worker in-process + Next.js dev | Berjalan di satu mesin Windows tanpa layanan eksternal |
| STT `small` int8 di CPU | ~1.0× realtime terukur di host ini; lihat `metrics.md` |
| Render | `RENDER_SLOTS`/`STT_SLOTS` memakai semaphore lokal in-process |

Semua pekerjaan berjalan di satu mesin: antrean render yang panjang tidak
menahan job baru karena tiap tahap punya thread pool sendiri (§6).

Sumber: probe host; `metrics.md`; T8.

---

## 6. Batas Paralel Lokal (pengganti QoS container)

Ukuran thread pool per tahap **adalah** batas paralelnya
(`clipper_shared.dispatcher`):

| Kontrol | Nilai | Catatan |
|---|---|---|
| `RENDER_SLOTS` | **1** (bawaan) | Pool `render`: satu task FFmpeg/MediaPipe sekaligus |
| `STT_SLOTS` | **1** (bawaan) | Pool `stt`: satu transkripsi sekaligus |
| `INGEST_WORKERS` | `2` (bawaan) | Pool `ingest`: lebih banyak menunggu jaringan |
| Anak proses dibuang setelah 1 task | `--max-tasks-per-child=1` (setara) | Mencegah bocor memori FFmpeg/MediaPipe menumpuk antar job |

**Tujuan batas ini:** API harus tetap responsif selama render berjalan —
beban berat tidak boleh dijalankan di luar pool-nya.

`RENDER_SLOTS`/`STT_SLOTS` **BELUM DITENTUKAN** nilai finalnya untuk host ini —
menaikkannya hanya setelah kapasitas terukur (`metrics.md`).

Sumber: TECH_SPEC §4.2, §4.3; `clipper_shared.dispatcher`; T9.

---

## 7. FORBIDDEN

| # | Larangan | Alasan |
|---|---|---|
| **F1** | ~~Memakai hasil test di Python 3.14 sebagai bukti kompatibilitas image produksi~~ | Tidak berlaku sejak T9: tidak ada image; 3.12–3.14 didukung setara, dan bukti 3.12 datang dari job CI. |
| **F2** | Menjalankan FFmpeg/STT/render di luar `clipper_shared.dispatcher` (thread/process sendiri) | Melewati batas pool `RENDER_SLOTS`/`STT_SLOTS`; API membeku saat render berjalan |
| **F3** | Mengandalkan pembersihan otomatis OS untuk retensi 48 jam | Tidak ada lifecycle rule object storage; wajib `clipper_shared.maintenance.purge_expired_raw_media` |
| **F4** | ~~Membuat scaffold kode / docker-compose sebagai bagian dari tugas dokumen~~ | **DIBATALKAN** — D4 tidak lagi berlaku; kode boleh dan memang sedang ditulis (lihat `README.md`) |
| **F5** | Menaikkan `RENDER_SLOTS`/`STT_SLOTS` tanpa mengukur kapasitas host lokal | Tanpa pengukuran, render/STT yang berlebih membuat API lambat |
| **F6** | Mengklaim target OKR kecepatan PRD tercapai | Melanggar D1; lihat `metrics.md` |
| **F7** | Menyalakan PostgreSQL/Redis/Celery untuk development lokal | Mode distributed dihapus (T9); jalur lokal selalu satu proses Standalone |
| **F8** | Menjalankan layanan lokal di konsol bersama tanpa isolasi | `uvicorn --reload` mengirim `CTRL_C_EVENT` ke seluruh konsol; pakai `npm run dev` (konsol tersembunyi per layanan) |

Sumber: TECH_SPEC §0 (D1), §3, §4.2, §4.3; probe host; T9.

---

## 8. Environment Dev Resmi

| Komponen | Pilihan | Catatan |
|---|---|---|
| Jalur dev | **Windows native Standalone** | `npm run dev` atau `start.cmd`; satu jendela (T8) |
| Setup | `scripts/setup.cmd` otomatis | `.env`, `.venv-win` + pip, FFmpeg, model wajah, `npm ci` |
| Python lokal | **3.14.6** di `.venv-win` | 3.12–3.14 didukung setara (T9) |
| Basis data lokal | SQLite `output/clipper.db` | Dibuat otomatis saat API start |
| Node | **22 LTS** untuk frontend | Host punya v24.13.1 — jalan dengan peringatan |

Sumber: probe host; TECH_SPEC §2, §1; T8.

---

## 9. Riwayat (dibatalkan oleh T9)

Rencana awal memakai **Docker/VPS**: tiga container (api, worker-light,
worker-render) di Hetzner CCX + Dokploy + Traefik, PostgreSQL 16 + Redis 7 +
Celery sebagai queue, Cloudflare R2 sebagai object storage, `cpu.max` cgroup
per container, `pg_dump` harian + restore drill, dan `docker-compose.yml` +
Makefile + `scripts/validate_compose.py` sebagai alat operasinya. Rencana itu
**dibatalkan seluruhnya oleh T9**: tidak ada Docker, WSL, PostgreSQL, Redis,
Celery, R2, maupun deployment server; aplikasi berjalan lokal sebagai satu
proses. Detail keputusan ada di `decisions.md` (D3, T2, T3, T4, T9).

---

## 10. Belum Ditentukan

- **BELUM DITENTUKAN** — Nilai `RENDER_SLOTS`/`STT_SLOTS` final untuk host ini
  (baru boleh dinaikkan setelah pengukuran kapasitas).
- **BELUM DITENTUKAN** — Penyedia & anggaran proxy residensial untuk ingest YouTube.
- **BELUM DITENTUKAN** — Apakah GPU akan ditambahkan (D1 hedge disebut di §5.1,
  jalurnya belum ditetapkan).
- **BELUM DITENTUKAN** — Skema backup data lokal `output/` (SQLite + media)
  bila pengguna ingin salinan aman; belum ada keputusan.

Sumber: TECH_SPEC §4.1, §4.3, §5.1, §8; probe host; T9.
