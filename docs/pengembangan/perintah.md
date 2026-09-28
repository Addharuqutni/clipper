# Perintah & test

## Menjalankan

| Perintah | Fungsi |
|---|---|
| `npm run dev` | Setup bila perlu, lalu API + web dalam satu terminal. |
| `start.cmd` | Sama, dalam jendela sendiri, plus membuka browser. |
| `stop.cmd` | Hentikan proses proyek ini yang tertinggal dan bebaskan port 8000/3000. |
| `npm run dev:web` | Hanya frontend Next.js. |
| `scripts\setup.cmd` | Hanya setup (idempoten; memasang ulang dependensi bila `pyproject.toml`/`package-lock.json` berubah). |

## Test dan pemeriksaan kode

Pasang alat dev sekali: `.venv-win\Scripts\python.exe -m pip install -e ".[all]"`.

**Python** (dari root repo):

```cmd
.venv-win\Scripts\python.exe -m pytest
.venv-win\Scripts\python.exe -m ruff check apps packages
.venv-win\Scripts\python.exe -m mypy apps/api/app packages/shared/src apps/worker-light/worker_light apps/worker-render/worker_render
```

Test API memakai basis data dan folder penyimpanan sementara
(`apps/api/tests/conftest.py`), jadi tidak menyentuh `output\clipper.db`.
Test render yang menjalankan FFmpeg sungguhan memakai `.libs\ffmpeg\` bila
ada.

**Frontend:**

```cmd
npm run lint
npm run typecheck
npm test
npm run build
```

## Basis data

Tidak ada migrasi manual. Saat API start, tabel dibuat dan tabel yang
skemanya tertinggal dari model dibangun ulang dengan datanya tetap utuh
(`apps/api/app/db/session.py`). Kolom baru `NOT NULL` tanpa default tetap
butuh basis data dikosongkan.

## Health check

```cmd
curl http://localhost:8000/health         :: {"status":"ok",...}
curl http://localhost:8000/health/ready   :: {"status":"ready","checks":{"database":true}}
```
