# Windows: cara kerja launcher

Cara memakai ada di [Mulai cepat](../panduan/mulai-cepat.md). Halaman ini
menjelaskan apa yang terjadi di balik layar, untuk yang mengubah script.

## Script

| Berkas | Dipanggil oleh | Fungsi |
|---|---|---|
| `start.cmd` (root) | pengguna | Menjalankan `node scripts\dev.mjs --open` di jendela sendiri. |
| `stop.cmd` (root) | pengguna | Meneruskan ke `scripts\stop-all.cmd`. |
| `scripts\dev.mjs` | `npm run dev`, `start.cmd` | Menjalankan `setup.cmd`, lalu `run-api.cmd` + `run-web.cmd` dengan log gabungan. Ctrl+C menghentikan semua lewat `taskkill`. `--open` membuka browser saat port 3000 siap. |
| `scripts\setup.cmd` | `dev.mjs` | Setup idempoten: `.env`, `.venv-win` + pip, FFmpeg (dicek punya libass), model wajah, `npm ci`. Dependensi dipasang ulang bila `pyproject.toml`/`package-lock.json` berubah. |
| `scripts\_env.cmd` | semua launcher | Mengisi `ROOT`, `VENV`, `PY`. |
| `scripts\run-api.cmd` | `dev.mjs` | uvicorn di `127.0.0.1:8000`, tanpa `--reload` (reload membunuh job yang sedang berjalan). |
| `scripts\run-web.cmd` | `dev.mjs` | `next dev` di port 3000. |
| `scripts\stop-all.cmd` + `stop-all.ps1` | `stop.cmd` | Menghentikan proses python/node/cmd yang command line-nya memuat folder repo ini, lalu menunggu port bebas. Proses proyek lain tidak disentuh. |

Semua `.cmd` wajib berakhiran baris **CRLF** (dipaksa `.gitattributes`):
dengan LF, `cmd.exe` bisa salah mencari label `call :x`/`goto :x`.

## Jebakan khas Windows

### `.env` dimuat API, bukan batch

`apps/api/app/core/config.py` memuat `.env` lewat python-dotenv ke environment
proses. Mengurainya di batch (`for /f ... delims==`) salah untuk nilai berkutip,
spasi di sekitar `=`, dan komentar berindentasi.

### Tiap layanan butuh konsol tersembunyi sendiri

Ctrl+C (`CTRL_C_EVENT`) dikirim ke **semua** proses di konsol yang sama.
`dev.mjs` karena itu memakai `spawn(..., { windowsHide: true, stdio: pipe })`
sehingga tiap layanan mendapat konsol tersembunyi sendiri dan hanya berhenti
lewat `taskkill`.

**Jangan pakai `detached: true`**: itu `DETACHED_PROCESS` (tanpa konsol sama
sekali), dan `cmd.exe` bisa menggantung pada perintah ber-pipe.

Karena anak tidak menerima Ctrl+C dari jendela utama, `dev.mjs` menghentikannya
dengan `taskkill /T /F`. Kode keluar `0xC000013A` (`STATUS_CONTROL_C_EXIT`)
berarti berhenti normal, bukan crash.

### `start.cmd` menyerahkan jendela ke Node

`start.cmd` memanggil `start "ClipperAI" node ...` lalu selesai. Tanpa batch
yang menunggu, Ctrl+C tidak memunculkan `Terminate batch job (Y/N)?`.

## Versi yang diuji

| | Host ini | Didukung |
|---|---|---|
| Python | 3.14.6 | 3.12–3.14 |
| Node | 24.13.1 | 22+ |

## Bug yang pernah terjadi di jalur ini

Ketiganya gagal **diam-diam** dengan pesan yang menyesatkan. Dicatat agar
tidak terulang.

1. **yt-dlp tidak menemukan FFmpeg → merge dilewati.** yt-dlp tidak error;
   ia meninggalkan video dan audio terpisah, dan berkas audio yang terpilih.
   *Perbaikan:* `--ffmpeg-location` dari `clipper_shared.storage.binary`, dan
   jalur hasil dibaca dari `--print after_move:filepath` (`worker_light/media_fetcher.py`).
2. **Worker gagal mendekripsi API key AI** (`No module named 'app'`). Impor
   `app.core.security` dari worker gagal, ditelan `except` lebar, dan skoring
   mati dengan "penyedia memerlukan API key". *Perbaikan:* `TokenCipher` pindah
   ke `clipper_shared.security`; dijaga `packages/shared/tests/test_security_location.py`.
3. **Label segmen melebihi kolom basis data.** Batas 64 karakter tersebar di
   tiga tempat dengan nilai berbeda. *Perbaikan:* satu konstanta
   `MAX_LABEL_CHARS` di `clipper_shared.scoring`; dijaga
   `apps/worker-light/tests/test_segment_label_bounds.py`.
