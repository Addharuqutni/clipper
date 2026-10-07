# Pemecahan masalah

| Gejala | Penyebab | Tindakan |
|---|---|---|
| `Penyedia AI belum siap: ...` saat mengirim video | Pengaturan AI kosong atau tidak lengkap | Isi di `/settings` ([Pemakaian → Penyedia AI](pemakaian.md#1-isi-penyedia-ai)). |
| `Konfigurasi penyedia AI tidak sah` | API key kosong pada preset `custom` | Isi kunci, atau pakai preset `Ollama (lokal)` untuk server tanpa kunci. |
| Menyimpan setelan gagal dengan pesan soal izin | URL menunjuk `localhost`/LAN tanpa izin | Centang **Izinkan alamat lokal/privat**. |
| Video ditolak saat ingest: `melebihi batas N menit` | Video lebih panjang dari batas model atau `MAX_VIDEO_DURATION_MIN` | Pakai model berkonteks lebih besar, naikkan `MAX_VIDEO_DURATION_MIN` di `.env`, atau video lebih pendek. |
| Analisis gagal: `melebihi kapasitas konteks` | Transkrip lebih panjang dari konteks model (biasanya video upload) | Sama seperti di atas. |
| Jumlah klip lebih sedikit dari yang diminta | Dibatasi 1 klip per 3 menit video, atau AI tidak menemukan cukup momen | Lihat log job di halaman detail. |
| `Penyedia membalas tanpa isi yang dapat dibaca` saat Uji koneksi | Model penalaran/proxy mengisi `reasoning_content` tetapi `content` kosong, atau anggaran token jawaban habis sebelum model selesai berpikir | Perbarui aplikasi (uji koneksi kini memakai anggaran token yang cukup dan membaca `reasoning_content`). Bila tetap muncul, coba model non-penalaran. |
| YouTube: format hilang, atau `No supported JavaScript runtime` | Node.js tidak ada di `PATH` | Pasang Node.js 22+ lalu jalankan ulang. |
| Job `Gagal` dengan pesan "Terhenti karena aplikasi ditutup" | Aplikasi dimatikan saat job diproses | Buka job, tekan **Proses ulang**. |
| Analisis memakai "segmen otomatis tanpa AI" | Penyedia AI gagal/timeout setelah 4 percobaan | Periksa penyedia di `/settings`, lalu **Analisis ulang** di halaman job. |
| Render ulang ditolak: "Media sumber sudah tidak ada" | Video sumber dihapus 48 jam setelah render terakhir | Buat job baru dari video yang sama. |
| Port 8000/3000 sudah dipakai | Proses lama masih jalan (jendela ditutup paksa) | Klik dua kali `stop.cmd`. |
| `Terminate batch job (Y/N)?` setelah Ctrl+C | Pertanyaan bawaan `npm.cmd` di Command Prompt | Layanan sudah berhenti; jawab `Y`. |
| `Fontconfig error: ... File not found` di log | FFmpeg Windows tidak punya fontconfig | Abaikan — subtitle tetap dirender dengan font sistem. |
| `Exception in callback _ProactorBasePipeTransport._call_connection_lost()` + `ConnectionResetError: [WinError 10054]` di log `[api]` | Klien (browser) membatalkan pemutaran klip di tengah pengiriman berkas, jadi soket ditutup paksa (RST). Bukan kegagalan aplikasi: respons 206 sudah terkirim. Sejak perbaikan, ini turun ke level debug dan tidak lagi tampil sebagai traceback. | Abaikan. Bila masih muncul sebagai traceback, jalankan ulang aplikasi agar penanganan baru terpasang. |
| Setup gagal saat `pip install` | Versi Python di luar 3.12–3.14 | Pasang Python 3.12–3.14, hapus folder `.venv-win`, jalankan ulang. |
| Setup gagal mengunduh FFmpeg | Jaringan/firewall | Taruh `ffmpeg.exe` dan `ffprobe.exe` manual di `.libs\ffmpeg\` ([Instalasi manual](../pengembangan/instalasi-manual.md#ffmpeg)). |

## Port masih terpakai setelah `stop.cmd`

`next dev` membuat proses anak. Bila induknya mati mendadak, anaknya bisa
tetap memegang port. `stop.cmd` hanya menghentikan proses milik folder proyek
ini; bila port dipakai aplikasi lain, cek dan hentikan manual:

```cmd
netstat -ano -p tcp | findstr ":8000 :3000"
taskkill /F /T /PID <pid>
```

## Melihat log

Semua log tampil di jendela `npm run dev` / `start.cmd`, diberi awalan
`[api]` atau `[web]`. Riwayat tiap job juga tersedia di tab **Log proses**
pada halaman detail job.
