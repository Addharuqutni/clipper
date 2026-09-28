# ClipperAI

Mengubah video panjang (YouTube atau berkas lokal) menjadi klip vertikal 9:16
dengan subtitle karaoke. AI memilih momen terbaik, wajah pembicara dijaga di
tengah bingkai, dan hasilnya siap diunggah ke TikTok, Reels, atau Shorts.

Berjalan di Windows tanpa Docker, WSL, PostgreSQL, atau Redis.

## Mulai

Butuh **Python 3.12–3.14** dan **Node.js 22+**. Lalu, dari folder proyek:

```cmd
npm run dev
```

atau klik dua kali **`start.cmd`**. Pemakaian pertama memasang semuanya
otomatis (~10 menit). Buka <http://localhost:3000>, isi penyedia AI di
**Setelan**, lalu kirim video. Berhenti dengan Ctrl+C atau `stop.cmd`.

## Dokumentasi

Semua dokumentasi ada di folder [`docs/`](docs/README.md):

- [Mulai cepat](docs/panduan/mulai-cepat.md) — prasyarat, menjalankan, menghentikan
- [Pemakaian](docs/panduan/pemakaian.md) — penyedia AI, batas, lokasi hasil klip
- [Pemecahan masalah](docs/panduan/pemecahan-masalah.md)
- [Arsitektur](docs/pengembangan/arsitektur.md), [Konfigurasi](docs/pengembangan/konfigurasi.md), [Perintah & test](docs/pengembangan/perintah.md), [Windows](docs/pengembangan/windows.md)
