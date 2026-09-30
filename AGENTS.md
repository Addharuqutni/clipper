# AGENTS.md

## Bahasa dan scope

- Tulis kesimpulan, ringkasan, laporan akhir, dan pertanyaan kepada pengguna dalam Bahasa Indonesia.
- Ikuti konvensi repo untuk kode, nama simbol, pesan error, dan isi file teknis.
- Perubahan yang dibuat pengguna atau agen lain adalah milik pengguna. Jangan menimpa atau membuangnya tanpa instruksi eksplisit.

## Model proyek

ClipperAI adalah monorepo aplikasi lokal Windows native:

- `apps/web/` — frontend Next.js.
- `apps/api/` — API FastAPI.
- `apps/worker-light/` dan `apps/worker-render/` — kode pipeline Python.
- `packages/shared/` — dispatcher, database, storage, provider AI, reframe, dan subtitle bersama.
- `scripts/` — setup dan launcher Windows.
- `output/` — SQLite dan media lokal; `.work/`, `.models/`, `.libs/ffmpeg/`, `.venv-win/`, dan `node_modules/` adalah artefak lokal.

Jalur resmi development adalah **Windows Standalone**: satu proses API menjalankan pipeline in-process dengan SQLite dan storage lokal. Jangan mengasumsikan PostgreSQL, Redis, Celery, Docker, WSL, atau S3 tersedia untuk development lokal.

## Perintah resmi

Dari root repo:

```cmd
npm run dev
start.cmd
stop.cmd
```

- `npm run dev` atau `start.cmd` menjalankan setup otomatis, API di port `8000`, dan web di port `3000`.
- `stop.cmd` menghentikan proses proyek yang tertinggal.
- `npm run dev:web` menjalankan frontend saja.
- `scripts\setup.cmd` hanya menjalankan setup idempoten.
- Jalur manual dan detail launcher ada di [`docs/panduan/mulai-cepat.md`](docs/panduan/mulai-cepat.md), [`docs/pengembangan/instalasi-manual.md`](docs/pengembangan/instalasi-manual.md), dan [`docs/pengembangan/windows.md`](docs/pengembangan/windows.md).

API listen hanya di `127.0.0.1:8000`. Health check:

```cmd
curl http://localhost:8000/health
curl http://localhost:8000/health/ready
```

## Pemeriksaan perubahan

Pilih pemeriksaan sesuai scope perubahan. Jalankan dari shell bersih, bukan dari proses `npm run dev` yang sedang berjalan.

Frontend:

```cmd
npm run lint
npm run typecheck
npm test
npm run build
```

Python:

```cmd
.venv-win\Scripts\python.exe -m pytest
.venv-win\Scripts\python.exe -m ruff check apps packages
.venv-win\Scripts\python.exe -m mypy apps/api/app packages/shared/src apps/worker-light/worker_light apps/worker-render/worker_render
```

- Test Python memakai database dan storage sementara melalui konfigurasi test; jangan arahkan test ke `output\clipper.db` nyata.
- Perubahan frontend wajib minimal melewati test yang relevan dan `npm run typecheck`; perubahan UI atau build-critical juga perlu `npm run build`.
- Sebelum menyatakan pekerjaan selesai, jalankan smoke check pada jalur yang diubah, bukan hanya test unit.

## Aturan pengembangan

### Domain dan keputusan

Sebelum mengeksplorasi istilah atau keputusan domain, baca [`docs/agents/domain.md`](docs/agents/domain.md). Jika ada `CONTEXT.md`, `CONTEXT-MAP.md`, atau `docs/adr/`, ikuti file yang relevan; jika belum ada, lanjutkan tanpa membuatnya hanya untuk memenuhi aturan.

Gunakan istilah domain yang sudah dipakai kode dan dokumentasi. Jika perubahan bertentangan dengan keputusan terdokumentasi, sebutkan konflik tersebut secara eksplisit.

### Issue tracker lokal

Issue tidak memakai remote tracker. Saat diminta membuat atau menerbitkan issue:

- Simpan di `.scratch/<feature-slug>/`.
- Spesifikasi ada di `spec.md`.
- Ticket implementasi satu file per isu di `issues/<NN>-<slug>.md`, mulai dari `01`.
- Letakkan `Status:` dekat bagian atas; gunakan label dari [`docs/agents/triage-labels.md`](docs/agents/triage-labels.md).
- Komentar ditambahkan di bawah `## Comments`.
- Ikuti detail operasi di [`docs/agents/issue-tracker.md`](docs/agents/issue-tracker.md).

### Keamanan dan data lokal

- API tidak memiliki login dan hanya aman untuk akses lokal; pertahankan binding `127.0.0.1` dan validasi `Host`.
- API key provider AI dan cookies YouTube harus tetap terenkripsi dengan `TOKEN_ENCRYPTION_KEY`; jangan pernah mengembalikan API key tersimpan ke UI atau log.
- Pertahankan validasi SSRF untuk endpoint AI dan URL subtitle/provider. Jangan menambahkan bypass host lokal tanpa kebutuhan eksplisit.
- Jangan menghapus `output/clipper.db`, media, credential, atau file kerja pengguna sebagai bagian dari test atau cleanup tanpa instruksi eksplisit.
- Perubahan render harus mempertahankan seek frame-accurate saat subtitle digunakan, output canonical yang dirujuk database, dan output klip lokal yang diharapkan.

### Windows dan line ending

- File `.cmd` wajib CRLF; file `.sh`, Python, TypeScript, JSON, YAML, Markdown, dan TOML mengikuti aturan LF di `.gitattributes`.
- Launcher memakai konsol tersembunyi terpisah dan menghentikan child process dengan `taskkill`; jangan mengganti pola ini dengan konsol bersama atau `detached: true` tanpa alasan yang diverifikasi.
- Setelah Redis atau layanan eksternal direstart pada lingkungan non-standalone, restart worker yang bergantung padanya.

## Rujukan utama

- [`docs/panduan/pemakaian.md`](docs/panduan/pemakaian.md) — alur input, AI, job, render, dan hasil.
- [`docs/pengembangan/arsitektur.md`](docs/pengembangan/arsitektur.md) — pipeline dan struktur runtime.
- [`docs/pengembangan/konfigurasi.md`](docs/pengembangan/konfigurasi.md) — `.env`, storage, model, dan batas runtime.
- [`docs/pengembangan/perintah.md`](docs/pengembangan/perintah.md) — test, lint, typecheck, build, dan health check.
- [`docs/pengembangan/windows.md`](docs/pengembangan/windows.md) — launcher dan jebakan Windows.
- [`docs/agents/issue-tracker.md`](docs/agents/issue-tracker.md) — issue lokal.
- [`docs/agents/triage-labels.md`](docs/agents/triage-labels.md) — label triage.
- [`docs/agents/domain.md`](docs/agents/domain.md) — konteks domain dan ADR.
