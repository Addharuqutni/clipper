# Mulai cepat

ClipperAI mengubah video panjang (YouTube atau berkas lokal) menjadi klip
vertikal 9:16 dengan subtitle karaoke. Semua berjalan di komputer Windows
sendiri — tanpa Docker, WSL, PostgreSQL, atau Redis.

## Prasyarat

| Kebutuhan | Versi | Catatan |
|---|---|---|
| Windows | 10 (build 19045+) atau 11 | |
| Python | 3.12–3.14 | Dari [python.org](https://www.python.org); centang "Add to PATH". |
| Node.js | 22 atau lebih baru | Dari [nodejs.org](https://nodejs.org). |

FFmpeg, model AI, dan semua library dipasang otomatis.

## Menjalankan

Pilih salah satu:

- **Klik dua kali `start.cmd`** di folder proyek. Satu jendela terbuka, dan
  browser membuka <http://localhost:3000> sendiri.
- **Atau ketik** di terminal, dari folder proyek:

  ```cmd
  npm run dev
  ```

  Lalu buka <http://localhost:3000>.

**Pemakaian pertama butuh sekitar 10 menit.** Setup otomatis membuat
virtualenv Python, memasang library, mengunduh FFmpeg (~200 MB) dan model
wajah, menjalankan `npm ci`, serta membuat `.env` dengan kunci enkripsi acak.
Berikutnya aplikasi langsung jalan dalam hitungan detik.

Model transkripsi Whisper (~460 MB) diunduh saat video pertama ditranskripsi.

## Menghentikan

- Tekan **Ctrl+C** di jendela tadi, atau tutup jendelanya.
- Bila port 8000/3000 masih terpakai (mis. proses dimatikan paksa), klik dua
  kali **`stop.cmd`**.

Di Command Prompt, setelah Ctrl+C pada `npm run dev` muncul
`Terminate batch job (Y/N)?`. Layanan sudah berhenti; jawab `Y`. Pertanyaan
ini tidak muncul di PowerShell atau lewat `start.cmd`.

## Langkah pertama setelah jalan

1. Buka **Setelan** (`/settings`) dan isi penyedia AI. Tanpa ini, video
   ditolak saat dikirim. Lihat [Pemakaian → Penyedia AI](pemakaian.md#1-isi-penyedia-ai).
2. Buka **YouTube** atau **Upload**, pilih jumlah klip, lalu kirim.
3. Pantau prosesnya di **Dashboard**. Klip jadi muncul di `output\clips\<job_id>\`.

## Alamat

| Layanan | URL |
|---|---|
| Aplikasi | <http://localhost:3000> |
| API | <http://localhost:8000> |
| Dokumentasi API | <http://localhost:8000/docs> |
| Health check | <http://localhost:8000/health> |
