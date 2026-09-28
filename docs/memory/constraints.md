# Constraints — Batasan Operasional & Lingkungan

> **Catatan 2026-09-27 (T9):** Docker, WSL, PostgreSQL, Redis, dan pin Python 3.12 tidak lagi relevan — aplikasi berjalan lokal saja (Python 3.12–3.14). Bagian terkait disimpan sebagai riwayat.

File ini adalah **filter pertama** sebelum menjalankan apa pun secara lokal.
Banyak perintah yang "wajar" di laptop lain akan **salah** di environment ini.

Sumber: TECH_SPEC §4, §6 (Sprint 0), §2.

---

## 1. Hasil Probe Host — TERVERIFIKASI

> **Koreksi terhadap TECH_SPEC §1.1.** Data di bawah ini adalah hasil probe
> langsung dan **lebih akurat** daripada asumsi di TECH_SPEC §1.1. Bila
> keduanya berbenturan, **bagian ini yang menang**.

### 1.1 Windows host (jalur eksekusi lokal — T8)

| Item | Status terverifikasi | Implikasi |
|---|---|---|
| OS host | **Windows 10 (19045)** | Jalur lokal resmi: **Standalone native** (`npm run dev` / `start.cmd`) |
| Python host | **3.14.6** di `.venv-win` | Cukup untuk API + worker in-process (wheel `cp314` tersedia); pin 3.12 hanya untuk image container (T3) |
| Node host | **v24.13.1** | Target 22 LTS; npm memberi `EBADENGINE` tetapi `next dev` jalan |
| `nvidia-smi` | tidak ada | Tidak ada GPU → konsisten dengan D1 (CPU-only) |
| `ffmpeg` | `.libs/ffmpeg/` (build BtbN, diunduh `setup.cmd`) | `libass` + `libx264` terverifikasi; dipakai lewat `FFMPEG_BINARY`, bukan `PATH` |
| PostgreSQL / Redis | **tidak dipakai** lokal | SQLite `output/clipper.db` + runner in-process + `LocalEventBus` |
| `docker` | tidak ada | Hanya relevan untuk deployment VPS (D3) |

### 1.2 WSL2 (historis — tidak lagi dipakai, lihat T8)

| Item | Status terverifikasi (2026-09-20) |
|---|---|
| Distro | **Ubuntu 26.04 LTS ("resolute")** |
| vCPU | **2 vCPU** |
| RAM total | **1.9 GiB** (~**1.3 GiB** tersedia) |
| Swap | **3 GiB** |
| Python | **3.14.4** — hanya `/usr/bin/python3.14` |
| `ffmpeg` | **8.0.1** ✅ |
| Docker | **TIDAK ADA**; `systemctl is-active docker` = **inactive** |

Sumber: probe host langsung (verifikasi pengguna, 2026-09-20; diperbarui 2026-09-26), menggantikan TECH_SPEC §1.1.

---

## 2. Python — 3.12 untuk Container, 3.14 untuk Lokal

- **Image container produksi WAJIB** Python **3.12.x** (base
  `python:3.12-slim-bookworm`) — T3.
- **Lokal Windows** memakai Python **3.14.6** di `.venv-win`
  (`pyproject.toml` menerima `>=3.12,<3.15`). `mediapipe`, `ctranslate2`
  (`faster-whisper`), dan `opencv-python-headless` menyediakan wheel `cp314`.
- Suite yang lulus di 3.14 **bukan** bukti kompatibilitas 3.12; bukti sah:
  `make test-312`.

Sumber: TECH_SPEC §6 Sprint 0 butir 2; status instalasi dari probe host.

---

## 3. FFmpeg >= 7 — Wajib libx264 DAN libass

**Koreksi pin:** bukan lagi "FFmpeg 7.x" persis, melainkan
**FFmpeg >= 7 dengan libx264 DAN libass**.

| Lokasi | Status verifikasi |
|---|---|
| Lokal Windows `.libs/ffmpeg/` | ✅ build BtbN `N-126755+`, `--enable-libass` + `--enable-libx264` |
| WSL2 (historis) | ✅ 8.0.1, `libx264-165`, `libass9` |

> ⚠️ Banyak paket FFmpeg distro **tidak** menyertakan `libass` — jangan
> berasumsi image/container lain otomatis aman, verifikasi per image.

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

## 4. Docker & WSL — Hanya Relevan untuk Deployment

- Lokal **tidak** memakai Docker maupun WSL (T8). Tidak ada lagi alasan
  memasang Docker di workstation untuk development.
- `docker-compose.yml` dan Dockerfile tetap menjadi definisi deployment
  VPS (D3). Docker belum terpasang di workstation ini, jadi compose hanya
  divalidasi statis (`scripts/validate_compose.py` lewat Makefile).

Sumber: probe host; TECH_SPEC §1.1; T8.

---

## 5. Kapasitas Lokal

| Beban | Catatan |
|---|---|
| API + worker in-process + Next.js dev | Berjalan di satu mesin Windows tanpa layanan eksternal |
| STT `small` int8 di CPU | ~1.0× realtime terukur di host ini; lihat `metrics.md` |
| Render | `RENDER_SLOTS`/`STT_SLOTS` memakai semaphore lokal in-process |

Batas WSL2 1.9 GiB (§1.2) tidak lagi relevan karena WSL tidak dipakai.

Sumber: probe host; `metrics.md`; T8.

---

## 6. QoS & Isolasi CPU

| Kontrol | Nilai | Catatan |
|---|---|---|
| `RENDER_SLOTS` | **1** | Worker render hanya satu task sekaligus |
| `STT_SLOTS` | **1** | Worker STT hanya satu task sekaligus |
| `render_concurrency` | `1`, `--max-tasks-per-child=1` | Proses anak dibuang setelah 1 task (bocor memori FFmpeg/MediaPipe) |
| `cpu.max` (cgroup v2) | **isolasi wajib** per container | Mencegah render membekukan API |
| vCPU dedicated | CCX, bukan CX (shared) | Shared vCPU → waktu render tak terprediksi |
| Admission control | wajib | Tanpa auto-scale, tolak job baru saat kapasitas penuh |

**Tujuan isolasi:** API (container A) harus tetap responsif selama render
berjalan. Ini divalidasi eksplisit di Sprint 5 butir 39 ("uji starvation CPU").

`RENDER_SLOTS` final **BELUM DITENTUKAN** — bergantung spesifikasi VPS (TECH_SPEC §8 butir 1).

Sumber: TECH_SPEC §4.2, §4.3, §6 Sprint 5 butir 39.

---

## 7. FORBIDDEN

| # | Larangan | Alasan |
|---|---|---|
| **F1** | ~~Memakai hasil test di Python 3.14 sebagai bukti kompatibilitas image produksi~~ | Tidak berlaku sejak T9: tidak ada image; 3.12–3.14 didukung setara. |
| **F2** | Menjalankan FFmpeg/STT/render di container **api** | Melanggar pemisahan 3-container; API membeku saat render |
| **F3** | Mengandalkan **hanya** lifecycle rule R2 untuk retensi 48 jam | Lifecycle rule berbasis prefix+umur, bukan kejadian (§3 TECH_SPEC). Wajib maintenance task |
| **F4** | ~~Membuat scaffold kode / docker-compose sebagai bagian dari tugas dokumen~~ | **DIBATALKAN** — D4 tidak lagi berlaku; kode boleh dan memang sedang ditulis (lihat `README.md`) |
| **F5** | Menaikkan `RENDER_SLOTS`/`STT_SLOTS` tanpa mengukur kapasitas VPS | Tanpa auto-scale, menyebabkan CPU starvation |
| **F6** | Mengklaim target OKR kecepatan PRD tercapai | Melanggar D1; lihat `metrics.md` |
| **F7** | Menyalakan PostgreSQL/Redis/Celery untuk development lokal | Lokal selalu Standalone (T8); `_env.cmd` memaksa `STANDALONE=true` |
| **F8** | Menjalankan layanan lokal di konsol bersama tanpa isolasi | `uvicorn --reload` mengirim `CTRL_C_EVENT` ke seluruh konsol; pakai `npm run dev` (konsol tersembunyi per layanan) |

Sumber: TECH_SPEC §0 (D1), §3, §4.2, §4.3, §6 Sprint 0 butir 2; probe host.

---

## 8. Environment Dev Resmi

| Komponen | Pilihan | Catatan |
|---|---|---|
| Jalur dev | **Windows native Standalone** | `npm run dev` atau `start.cmd`; satu jendela (T8) |
| Setup | `scripts/setup.cmd` otomatis | `.env`, `.venv-win` + pip, FFmpeg, model wajah, `npm ci` |
| Python lokal | **3.14.6** di `.venv-win` | Container tetap 3.12 |
| Basis data lokal | SQLite `output/clipper.db` | Dibuat otomatis saat API start |
| Orkestrasi prod | Dokploy + Traefik di Hetzner; `docker-compose.yml` (Postgres 16, Redis 7.2, 3 container app) | D3 |
| Node | **22 LTS** untuk frontend | Host punya v24.13.1 — jalan dengan peringatan |

Sumber: probe host; TECH_SPEC §2, §1; T8.

---

## 9. Konsekuensi Backup (akibat D3)

VPS self-managed **tidak** punya backup terkelola. Karena itu, sebelum Beta:

- `pg_dump` harian → R2, dan
- **restore drill** (bukan hanya membuat backup) — Sprint 5 butir 41.

Sumber: TECH_SPEC §4.4, §6 Sprint 5 butir 41.

---

## 10. Belum Ditentukan

- **BELUM DITENTUKAN** — Spesifikasi VPS Hetzner (vCPU, RAM, tipe instance) →
  menentukan nilai `RENDER_SLOTS`/`STT_SLOTS` final.
- **BELUM DITENTUKAN** — Nilai `cpu.max` konkret per container (baru disebut
  "isolasi wajib", bukan angka).
- **BELUM DITENTUKAN** — Penyedia & anggaran proxy residensial untuk ingest YouTube.
- **BELUM DITENTUKAN** — Apakah GPU akan ditambahkan (D1 hedge disebut di §5.1,
  jalurnya belum ditetapkan).
- **BELUM DITENTUKAN** — Cara memasang Docker di mesin deployment/CI agar
  `docker-compose.yml` diuji eksekusi, bukan hanya validasi statis.

Sumber: TECH_SPEC §4.1, §4.3, §5.1, §8; probe host.
