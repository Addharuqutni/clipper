# Product Requirement Document (PRD)
## Project Name: ClipperAI (AI Video Repurposing Platform)
**Version:** 1.0.0 (MVP)  
**Status:** Ready for Review  
**Target Delivery:** Phase 1 (MVP)

> **Catatan 2026-09-27:** implementasi berbeda di beberapa titik — batas durasi mengikuti konteks model AI (maks 180 menit, bukan 60), satu pengguna lokal tanpa login (bukan multi-user), tanpa publishing (D5). Lihat [memory/decisions.md](../memory/decisions.md).

---

## 1. Executive Summary & Objective

### 1.1 Overview
ClipperAI adalah platform SaaS bertenaga kecerdasan buatan (AI) yang mengubah video berdurasi panjang (*long-form video*) dari tautan YouTube maupun berkas video lokal menjadi klip video vertikal (9:16) siap unggah. Sistem secara otomatis menganalisis momen paling menarik, melakukan reframing wajah/subjek secara dinamis, menghasilkan takarir kinetik. Klip final diunduh sebagai MP4 siap unggah ke TikTok, Instagram Reels, maupun YouTube Shorts.

### 1.2 Core Objectives
* Memangkas waktu pembuatan konten pendek dari rata-rata 60–90 menit menjadi kurang dari 5 menit per klip.
* Mengotomatisasi kurasi momen krusial (*hooks*, puncak emosi, punchline, narasi tuntas) menggunakan analisis multimodal/transkrip AI.
* Memberikan alur kerja menyeluruh (*all-in-one workflow*) mulai dari input video mentah hingga klip siap unggah.

### 1.3 Key Success Metrics (OKRs)
* **Processing Speed:** Waktu pemrosesan video 30 menit selesai dalam $\le 5$ menit.
* **Reframing Accuracy:** Tingkat presisi pelacakan wajah (*active speaker detection*) $\ge 85\%$.
* **Export Rate:** Minimal 65% klip yang direkomendasikan AI diunduh oleh pengguna.
* **Upload Reliability:** Keberhasilan unggah berkas besar (> 1 GB) mencapai $\ge 98\%$.

---

## 2. User Personas

| Persona | Profil & Karakteristik | Kebutuhan Utama | Titik Masalah (*Pain Points*) |
|---|---|---|---|
| **Content Creator / Podcaster** | Memproduksi podcast, webinar, atau ulasan panjang mingguan. | Mengubah 1 episode panjang menjadi 5–10 cuplikan vertikal secara cepat. | Proses manual mencari cap waktu (*timestamp*) dan memotong video sangat repetitif. |
| **Social Media Manager (Agency)** | Mengelola beberapa akun klien sekaligus di TikTok dan Reels. | Volume konten tinggi, konsistensi gaya takarir, dan kemudahan penjadwalan. | Alur kerja terfragmentasi (unduh, edit manual, simpan, pindah ke aplikasi medsos untuk posting). |

---

## 3. User Journey & Product Workflow

```
[User Input: YouTube URL atau File Lokal >1 GB]
                        │
                        ▼
           [Pre-Processing & Validation]
    (Direct S3/R2 Multipart Upload / YouTube Fetch)
                        │
                        ▼
      [ASR: Audio Extraction & Transcription]
            (Whisper Timestamped Output)
                        │
                        ▼
      [AI Scoring Engine & Segment Selection]
         (LLM Evaluates Hooks, Pacing, & Story)
                        │
                        ▼
         [Video Generation & Rendering]
    (Auto-Crop 9:16 + Active Speaker + Kinetic Subs)
                        │
                        ▼
           [Dashboard / Review Studio]
     (Score Review, Quick Edit, Caption Generator)
                        │
                        │
                        ▼
                 [Download MP4]
```

---

## 4. Functional Requirements (FR)

### Module 1: Media Ingestion
* **FR-1.1 (YouTube URL Ingestion):**
  * Input tautan valid dari YouTube (`watch?v=`, `youtu.be/`).
  * Sistem memvalidasi ketersediaan video, status akses (hanya publik/unlisted), serta batasan durasi (maksimal 60 menit untuk MVP).
  * **Resolusi Unduhan:** Dibatasi maksimal **1080p** (`bv*[height<=1080]+ba`) demi efisiensi CPU dan stabilitas decode/render.
* **FR-1.2 (Direct Multipart Upload for Large Files):**
  * Pengguna dapat mengunggah berkas lokal berukuran hingga 3 GB. Format yang didukung: `.mp4`, `.mov`, `.mkv`.
  * **Arsitektur Unggah:** Menggunakan mekanisme *Client-to-Storage Direct Upload* via *Presigned URLs* (S3 / Cloudflare R2). Berkas dipotong menjadi partisi kecil (*chunked upload*, 10 MB–20 MB per *part*).
  * Sistem menyediakan antarmuka *drag-and-drop*, progres persentase waktu nyata (*real-time progress bar*), serta kapabilitas *retry* otomatis jika koneksi terputus di tengah jalan.

### Module 2: Transcription & AI Analysis Engine
* **FR-2.1 (Timestamped Transcription & Diarization):**
  * Ekstraksi audio dan transkripsi kata per kata (*word-level timestamps*).
  * Pemisahan pembicara (*speaker diarization*) untuk mendeteksi siapa yang sedang berbicara di setiap detik.
* **FR-2.2 (Viral Scoring Engine):**
  * LLM menganalisis struktur percakapan untuk memilih 3–10 segmen potensial (durasi: 30–60 detik per segmen).
  * Parameter penilaian:
    * *Hook Quality* (3 detik pertama mengandung premis menarik/pertanyaan retoris).
    * *Completeness* (cerita memiliki struktur pengantar, isi, dan kesimpulan utuh tanpa jeda canggung).
    * *Emotional Arc* (tingkat penekanan kata, dinamika intonasi, tawa, atau reaksi spontan).
  * Setiap segmen diberi skor (1–100) dan label ringkasan (contoh: *"Penjelasan Kunci"*, *"Debat Kontroversial"*).

### Module 3: Video Re-Framing & Subtitles
* **FR-3.1 (Smart Auto-Reframe 9:16):**
  * Pelacakan wajah dan penentuan frame subjek (*face & pose detection*) agar pembicara aktif selalu berada di tengah orientasi vertikal.
  * *Split-screen Mode:* Otomatis membagi layar atas-bawah jika terdapat 2 pembicara yang saling berdialog aktif.
* **FR-3.2 (Dynamic Kinetic Subtitles):**
  * Menghasilkan takarir otomatis dengan animasi pergantian warna kata (*karaoke effect*).
  * Menyediakan preset gaya teks (warna font, stroke/outline, background box, penempatan atas/tengah/bawah).
  * Fitur penyuntingan manual pada transkrip sebelum diekspor jika terdapat kesalahan fonetik/kata.

### Module 4: Exporting
> Publikasi langsung ke platform sosial (OAuth TikTok/Meta, Publish Now, Schedule Post) **dihapus dari scope** (TECH_SPEC D5). Pengguna mengunduh MP4 lalu mengunggah manual.

* **FR-4.2 (AI Caption & Hashtag Generator):**
  * Generator takarir otomatis berbasis AI yang menyusun rangkuman singkat dan tagar relevan, siap disalin saat unggah manual.
* **FR-4.3 (Manual Download & Output Resolutions):**
  * Opsi unduh langsung berkas MP4 beresolusi:
    * **Preview:** 540x960 (9:16) untuk verifikasi cepat di browser.
    * **Final:** Full HD 1080x1920 (9:16) dengan bitrate optimal (CRF 18) siap tayang di TikTok, Reels, dan Shorts.

---

## 5. Non-Functional Requirements (NFR)

* **Dual Execution Modes (Scalability & Portability):**
  * **Distributed Mode (Cloud / VPS / Multi-Container):** Menggunakan PostgreSQL 16 + Redis 7 + Celery workers terpisah (`worker-light` dan `worker-render`) untuk skalabilitas multi-user.
  * **Standalone Mode (Windows Native / Local Single-User):** Berjalan langsung di host tanpa Docker, WSL, Postgres, atau Redis. Memanfaatkan SQLite tersemat (`aiosqlite`), in-process thread pool task runner, in-memory event bus SSE, dan local filesystem storage.
* **Performance & Processing Queue:**
  * Semua proses rendering video berat dan analisis AI dijalankan secara asinkron.
  * Dasbor pengguna memperbarui status tahapan secara waktu nyata via Server-Sent Events (SSE).
* **Storage & Lifecycle Management:**
  * Berkas mentah lokal (> 1 GB) dihapus otomatis dari bucket penyimpanan dalam kurun waktu 48 jam setelah proses pemotongan klip selesai.
  * Berkas klip final (9:16) disimpan selama 14 hari sebelum diarsipkan.
* **Security & Compliance:**
  * Enkripsi data saat transmisi (TLS 1.3) dan saat penyimpanan (AES-256-GCM).
  * Kredensial pihak ketiga (API key penyedia AI) disimpan terenkripsi di database.

---

## 6. Technical Stack Recommendations

| Layer | Teknologi yang Disarankan | Alasan Pemilihan |
|---|---|---|
| **Frontend** | Next.js (React), Tailwind CSS | Kompatibel untuk SSR, SEO, dan performa UI yang responsif. |
| **Backend API** | FastAPI (Python) | Unggul untuk integrasi pipeline AI/Python, async I/O, dan SSE. |
| **Task Queue** | Celery + Redis *(Distributed)* / In-Process ThreadPool *(Standalone)* | Memisahkan beban rendering berat; fleksibel untuk server maupun workstation lokal. |
| **Database** | PostgreSQL 16 *(Distributed)* / SQLite + aiosqlite *(Standalone)* | PostgreSQL untuk beban cloud; SQLite untuk kesederhanaan zero-dependency di Windows. |
| **Video Engine** | FFmpeg, OpenCV, MediaPipe | FFmpeg untuk slicing/rendering; MediaPipe untuk tracking wajah & reframe. |
| **Speech-to-Text** | Whisper API / Faster-Whisper (self-hosted CPU) | Transkripsi tingkat kata dengan akurasi tinggi dan dukungan multibahasa. |
| **AI Scoring** | LLM API (Gemini / Claude / DeepSeek / Ollama lokal) | Menganalisis konten teks berstempel waktu secara mendalam dan berbiaya efisien. |
| **Storage** | Cloudflare R2 / AWS S3 *(Cloud)* / Local Filesystem *(Dev/Standalone)* | Fleksibel antar object storage cloud dan disk lokal `output/`. |

---

## 7. Risks & Mitigation Strategies

| Risiko | Dampak | Strategi Mitigasi |
|---|---|---|
| **Biaya komputasi rendering membengkak** | Biaya operasional tinggi sebelum mencapai profit. | Batasi resolusi pratinjau (*preview*) di browser; render resolusi penuh hanya saat pengguna menekan tombol unduh. |
| **Pemblokiran IP saat mengunduh YouTube** | Ekstraksi video YouTube gagal di server. | Terapkan rotasi proxy residensial atau gunakan layanan pemrosesan media terkelola. |
| **Koneksi terputus saat upload file > 1 GB** | Pengguna frustrasi mengulang proses dari 0%. | Gunakan protokol *Tus.io* atau *S3 Multipart Upload* yang mendukung fitur *resumable upload*. |

---

## 8. Release Roadmap (MVP)

* **Sprint 1 (Fondasi & Ingestion):**
  * Setup arsitektur backend, database, dan antrean Redis.
  * Implementasi *direct S3 multipart upload* dan validasi tautan YouTube.
* **Sprint 2 (Pipeline Transkripsi & AI Scoring):**
  * Integrasi Whisper untuk transkripsi berstempel waktu.
  * Penyusunan prompt AI viral score dan parser segmen klip.
* **Sprint 3 (Video Engine & Auto-Framing):**
  * Implementasi skrip FFmpeg untuk crop 9:16 dan pelacakan wajah MediaPipe.
  * Integrasi takarir dinamis bergaya kinetik (*karaoke subtitles*).
* **Sprint 4 (Review Studio & Export):**
  * Pembuatan antarmuka Review & Quick Edit.
  * Unduhan MP4 final dan generator caption + hashtag.
* **Sprint 5 (Testing & Deployment):**
  * Uji beban (*stress testing*) unggah berkas besar dan antrean multi-user.
  * Peluncuran versi Closed Beta.