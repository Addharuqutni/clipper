# Referensi Eksternal

File ini mencatat sumber luar yang **sudah diverifikasi** dan memengaruhi
keputusan teknis ClipperAI. Tujuannya: saat implementasi Sprint 2–3 dimulai,
tidak perlu meneliti ulang dari nol.

> **Aturan:** pakai file ini sebagai titik awal, bukan sebagai kebenaran akhir.
> Konteks proyek referensi berbeda dari ClipperAI (lihat §3).

---

## 1. `jipraks/yt-short-clipper`

- **URL:** https://github.com/jipraks/yt-short-clipper
- **Bentuk:** aplikasi desktop Tauri (Rust shell) + sidecar Python
- **Fungsi:** mengunduh video YouTube, menemukan momen menarik via LLM, memotong
  klip 9:16 dengan pelacakan wajah dan subtitle terbakar
- **Studi dilakukan:** 2026-09-20

### 1.1 Modul yang Dipelajari dan Pengaruhnya

| File di repo referensi | Dipakai di | Pengaruh |
|---|---|---|
| `yt_short_clipper_core/portrait.py` | TECH_SPEC §5.2, Sprint 3 butir 26–29 | Algoritma pemilihan subjek, nilai tuning crop, penanganan pipe FFmpeg |
| `yt_short_clipper_core/caption_generator.py` | TECH_SPEC §5.2.1, Sprint 3 butir 30 | Format ASS satu-event-per-kata, penskalaan `PlayResX/Y` |
| `yt_short_clipper_core/highlight_finder.py` | TECH_SPEC §5.4, Sprint 2 butir 21a–21d, 22a | Pengerasan parsing LLM, pertahanan prompt injection, temperature |
| `yt_short_clipper_core/constants.py` | — | Pemetaan kode bahasa, penanganan bahasa `auto` |
| `requirements.txt` | — | Konfirmasi dependensi yang benar-benar dipakai |

### 1.2 Temuan Paling Bernilai

1. **Active speaker detection tanpa model ASD.** Repo ini memakai MediaPipe
   **Face Landmarker** + rasio bukaan mulut (24 lip-landmark) sebagai sinyal
   "sedang bicara", dengan bonus 2.0× dan penalti kontinuitas 0.4×. Ini
   mematahkan asumsi awal bahwa MediaPipe tidak dapat dipakai untuk masalah ini.
2. **Dua jebakan fatal yang harus dihindari:** (a) OpenCV BGR vs MediaPipe RGB —
   lupa `cvtColor` merusak akurasi secara diam-diam; (b) pipe FFmpeg **macet**
   bila stderr tidak dibuang oleh thread terpisah, karena buffer Windows ~64 KB.
3. **Subtitle tidak memakai tag karaoke `\k`.** Repo ini menghasilkan satu event
   `Dialogue` per kata dan mewarnai kata berjalan via `{\c&H00FFFF&}`.
4. **`PlayResX/Y` wajib dari resolusi nyata** hasil probe, bukan hardcoded
   1080×1920 — libass meregangkan kanvas sehingga subtitle gepeng di klip non-9:16.
5. **LLM tidak dapat dipercaya mengembalikan JSON bersih.** Solusinya: minta
   lebih banyak (`num_clips + 3`), selamatkan objek valid dengan `raw_decode()`,
   dan hantarkan pesan kesalahan yang membedakan sisi penyedia vs sisi pengguna.

---

## 2. Nilai Tuning Siap Pakai

Semua angka ini berasal dari kode yang berjalan, bukan tebakan.

| Konstanta | Nilai | Modul |
|---|---|---|
| `num_faces` | 10 | Face Landmarker |
| `min_face_detection_confidence` | 0.35 | Face Landmarker |
| `MIN_SPEAK_RATIO` | 0.18 | Deteksi bicara |
| `SPEAKER_BONUS` | 2.0 | Skor pemilihan wajah |
| `CONTINUITY_WEIGHT` | 0.4 | Skor pemilihan wajah |
| `EMA_ALPHA` | 0.15 | Penghalusan crop |
| `DEADZONE_FRAC` | 0.02 | Anti-jitter |
| `SNAP_FRAC` | 0.35 | Deteksi ganti adegan |
| `ENCODE_STALL_TIMEOUT_S` | 300 | Watchdog FFmpeg |
| Chunk subtitle | 4 kata | Generator ASS |
| `RANGE_MATCH_TOLERANCE_SECONDS` | 8.0 | Pencocokan rentang dari pengguna |
| `DEFAULT_TEMPERATURE` | 1.0 | LLM tanpa arahan |
| `DIRECTED_TEMPERATURE` | 0.3 | LLM dengan arahan |

---

## 3. Perbedaan Konteks yang Wajib Diingat

Repo referensi **bukan** cetak biru ClipperAI. Perbedaannya material:

| Aspek | Repo referensi | ClipperAI |
|---|---|---|
| Bentuk | Desktop (Tauri), satu pengguna | Web app; lokal satu pengguna (Standalone, T8), deployment VPS |
| Transkrip | Subtitle YouTube yang sudah ada | Subtitle YouTube bila ada, selain itu **Whisper sendiri** (TECH_SPEC D1) |
| Penyimpanan | Berkas lokal | Lokal: SQLite + `output/`; deployment: Cloudflare R2 + Postgres |
| Antrean | Proses lokal | Lokal: thread pool in-process; deployment: Celery + Redis |
| Publishing | Repliz API pihak ketiga | Dihapus (D5) |
| Runtime | Sidecar Python dibekukan | Lokal: `.venv-win` (Python 3.14); deployment: container Python 3.12 |

Konsekuensinya: **angka dan pola** dapat dipakai ulang; **arsitektur tidak**.

Sumber: TECH_SPEC §5.2, §5.2.1, §5.4, §9.1; Sprint 2–3.
