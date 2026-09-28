# Dokumentasi ClipperAI

Pilih sesuai kebutuhan. Setiap halaman berdiri sendiri dan cukup pendek untuk
dibaca sekali duduk.

## Memakai aplikasi

| Halaman | Isi |
|---|---|
| [Mulai cepat](panduan/mulai-cepat.md) | Prasyarat, menjalankan dengan satu perintah, menghentikan. **Mulai dari sini.** |
| [Pemakaian](panduan/pemakaian.md) | Alur kerja, mengisi penyedia AI, batas durasi & jumlah klip, lokasi hasil klip, mengosongkan data. |
| [Pemecahan masalah](panduan/pemecahan-masalah.md) | Gejala umum, penyebab, dan cara memperbaikinya. |

## Mengembangkan aplikasi

| Halaman | Isi |
|---|---|
| [Arsitektur](pengembangan/arsitektur.md) | Satu proses lokal, alur tahap, struktur direktori. |
| [Konfigurasi](pengembangan/konfigurasi.md) | Isi `.env`, nilai bawaan, variabel penting. |
| [Instalasi manual](pengembangan/instalasi-manual.md) | Memasang FFmpeg, Python, dan Node sendiri bila setup otomatis gagal. |
| [Perintah & test](pengembangan/perintah.md) | Menjalankan test, lint, typecheck, health check. |
| [Windows](pengembangan/windows.md) | Cara kerja script `.cmd`/`dev.mjs` dan jebakan khas Windows. |

## Latar belakang produk

| Halaman | Isi |
|---|---|
| [PRD](spesifikasi/PRD.md) | Kebutuhan produk (dokumen asli, v1.0.0). |
| [TECH_SPEC](spesifikasi/TECH_SPEC.md) | Spesifikasi teknis dan rencana sprint (dokumen asli, v1.0.0). |
| [Memori proyek](memory/README.md) | Ringkasan keputusan (ADR), batasan lingkungan, metrik, utang teknis, referensi. |

PRD dan TECH_SPEC adalah dokumen perencanaan awal. Bila berbeda dengan kode
atau halaman panduan di atas, **kode yang benar**; keputusan yang mengubahnya
tercatat di [memory/decisions.md](memory/decisions.md).
