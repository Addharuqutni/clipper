# Project Overview — ClipperAI

Ringkasan produk sesuai PRD v1.0.0 (MVP). Dokumen ini menjawab: *apa yang
dibangun, untuk siapa, dan lewat alur apa*.

Sumber utama: PRD §1, §2, §3, §4.

---

## 1. Tujuan Produk

ClipperAI adalah platform SaaS bertenaga AI yang mengubah video berdurasi panjang
(*long-form*) dari **tautan YouTube** maupun **berkas video lokal** menjadi klip
video vertikal **9:16** siap unggah.

Sistem secara otomatis:

- menganalisis momen paling menarik,
- melakukan reframing wajah/subjek secara dinamis,
- menghasilkan takarir kinetik (*kinetic subtitles*),
- memfasilitasi publikasi ke TikTok dan Instagram Reels.
  → *Catatan: butir terakhir ini **dihapus dari scope** oleh keputusan **D5**
  (produk download-only). Lihat `decisions.md`.*

### Core Objectives (PRD §1.2)

| # | Objective |
|---|---|
| 1 | Memangkas waktu pembuatan konten pendek dari rata-rata **60–90 menit** menjadi **< 5 menit per klip**. |
| 2 | Mengotomatisasi kurasi momen krusial (*hooks*, puncak emosi, punchline, narasi tuntas) via analisis multimodal/transkrip AI. |
| 3 | Memberikan alur kerja menyeluruh (*all-in-one*) dari input video mentah hingga penjadwalan/posting ke media sosial. |

> ⚠️ Objective #1 adalah **klaim OKR**, bukan hasil yang sudah terukur. Untuk
> angka realistis di jalur CPU-only, lihat `metrics.md` (TECH_SPEC §0.1).

Sumber: PRD §1.1, §1.2.

---

## 2. Persona

| Persona | Profil & Karakteristik | Kebutuhan Utama | Pain Points |
|---|---|---|---|
| **Content Creator / Podcaster** | Memproduksi podcast, webinar, atau ulasan panjang mingguan. | Mengubah 1 episode panjang menjadi 5–10 cuplikan vertikal secara cepat. | Proses manual mencari *timestamp* dan memotong video sangat repetitif. |
| **Social Media Manager (Agency)** | Mengelola beberapa akun klien sekaligus di TikTok dan Reels. | Volume konten tinggi, konsistensi gaya takarir, kemudahan penjadwalan. | Alur kerja terfragmentasi (unduh → edit manual → simpan → pindah ke aplikasi medsos untuk posting). |

Sumber: PRD §2.

---

## 3. User Journey & Product Workflow

Reproduksi alur dari PRD §3 sebagai teks (urutan tahap):

1. **User Input** — YouTube URL atau file lokal > 1 GB.
2. **Pre-Processing & Validation** — Direct S3/R2 Multipart Upload atau YouTube Fetch.
3. **ASR: Audio Extraction & Transcription** — Whisper timestamped output.
4. **AI Scoring Engine & Segment Selection** — LLM mengevaluasi *hooks*, *pacing*, dan *story*.
5. **Video Generation & Rendering** — Auto-crop 9:16 + active speaker + kinetic subtitles.
6. **Dashboard / Review Studio** — score review, quick edit, caption generator.
7. **Cabang keluaran** (dua jalur):
   - **Download MP4**, dan
   - **Direct Publish / Schedule** (TikTok API / Instagram Graph API).
     → *Jalur ini dihapus (D5). Hanya Download MP4 yang ada.*

Diagram ASCII asli dari PRD §3 tidak direproduksi di sini; lihat dokumen sumber.

Sumber: PRD §3.

---

## 4. Empat Modul Functional Requirements

| Modul | Nama | Isi ringkas |
|---|---|---|
| **Module 1** | Media Ingestion | Ingest YouTube URL + direct multipart upload file besar |
| **Module 2** | Transcription & AI Analysis Engine | Transkripsi word-level + diarization + viral scoring LLM |
| **Module 3** | Video Re-Framing & Subtitles | Auto-reframe 9:16 + kinetic subtitles karaoke |
| **Module 4** | Publishing & Exporting | Manual download + generator caption (publishing dihapus, D5) |

Sumber: PRD §4.

---

## 5. Daftar FR dengan Status Implementasi

Status per **2026-09-27** (setelah review menyeluruh dan perbaikan).

| FR | Deskripsi | Status |
|---|---|---|
| **FR-1.1** | YouTube URL Ingestion — `watch?v=`, `youtu.be/`, `shorts/`; publik/unlisted; batas durasi mengikuti konteks model AI (maks `MAX_VIDEO_DURATION_MIN`, default 180 menit; PRD semula 60) | **Implementasi** — URL dikanonikkan server, cookies tersimpan terenkripsi |
| **FR-1.2** | Upload berkas lokal hingga **3 GB** (`.mp4`/`.mov`/`.mkv`), per potongan 10 MB, progres, retry, resume | **Implementasi** — endpoint lokal per potongan (bukan presigned S3, lihat T9) |
| **FR-2.1** | Transkripsi bertimestamp per kata | **Implementasi** — diarization ditunda (`technical-debt.md`) |
| **FR-2.2** | Viral Scoring — 1–30 segmen (maks 1 per 3 menit), 25–65 detik, hook/completeness/emotional arc | **Implementasi** — retry 4x, cadangan tanpa AI bila penyedia gagal, analisis ulang |
| **FR-3.1** | Auto-reframe 9:16 (face track, black bars, blurred fill) | **Implementasi** — tanpa split-screen 2 pembicara |
| **FR-3.2** | Subtitle karaoke, preset gaya, font kustom, edit transkrip | **Implementasi** |
| **FR-4.1** | OAuth TikTok/Meta | **Dihapus dari scope** (D5) |
| **FR-4.2** | Caption + hashtag AI | **Implementasi** (per klip); publish/schedule dihapus (D5) |
| **FR-4.3** | Unduh MP4 1080×1920 | **Implementasi** |

Sumber: PRD §4; D2, D5, T9.

---

## 6. Cakupan MVP yang Sebenarnya (ringkas)

Berdasarkan TECH_SPEC §0 dan §6, MVP yang akan dibangun adalah:

Ingest (YouTube/upload) → transkripsi lokal CPU → viral scoring → render 9:16 +
subtitle karaoke → Review Studio → **export manual (download)**.

Yang **keluar** dari scope: OAuth TikTok/Meta, publish otomatis, penjadwalan (D5 — dihapus permanen),
diarization, auto-scale worker.

Sumber: TECH_SPEC §0, §5.3, §6.

---

## 7. Belum Ditentukan di Overview Ini

- **BELUM DITENTUKAN** — Model bisnis / tier pricing dan perlu tidaknya
  watermark (masih pertanyaan terbuka; TECH_SPEC §8 butir 4).
- **BELUM DITENTUKAN** — Spesifikasi VPS Hetzner (vCPU/RAM/tipe instance);
  TECH_SPEC §8 butir 1.

Sumber: TECH_SPEC §8; PRD §1, §2, §3, §4.
