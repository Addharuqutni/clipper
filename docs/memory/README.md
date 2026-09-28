# ClipperAI — Memory Index

Direktori ini adalah **memori proyek** untuk agent/developer baru. Isinya adalah
ringkasan yang dapat dilacak ke dua dokumen sumber:

- [`../spesifikasi/PRD.md`](../spesifikasi/PRD.md) — PRD v1.0.0 (MVP)
- [`../spesifikasi/TECH_SPEC.md`](../spesifikasi/TECH_SPEC.md) — Technical Specification & Task Plan v1.0.0

Seluruh isi memori ini ditulis dalam Bahasa Indonesia dengan istilah teknis
dipertahankan dalam Bahasa Inggris. Setiap file mencantumkan rujukan bagian
("Sumber: ...") agar klaim dapat diverifikasi.

## Status Repo

> ⚠️ **Verifikasi status terkini dengan membaca filesystem, jangan mengandalkan
> halaman ini.**

Kode sudah diimplementasikan: `apps/api/`, `apps/worker-light/`,
`apps/worker-render/`, `packages/shared/`, `apps/web/`, plus `pyproject.toml`
dan CI workflow (`.github/workflows/ci.yml`). `docs/memory/` berisi **7 file
memori + index ini**.

**Menjalankan lokal (T8/T9):** `npm run dev` atau klik dua kali `start.cmd` di
root repo. Selalu Windows Standalone — SQLite `output/clipper.db`, worker di
dalam proses API, tanpa PostgreSQL/Redis/Celery/WSL/Docker. Setup otomatis lewat
`scripts/setup.cmd`. Tidak ada Docker/VPS/container sama sekali (T9).

**Konsekuensi terhadap D4:** keputusan **D4** ("hanya dokumen, tanpa scaffold
kode") **sudah tidak berlaku**; rujukan sejarahnya tetap ada di `decisions.md`.

---

## Daftar File Memori

| File | Isi | Kapan dibaca |
|---|---|---|
| [`project-overview.md`](./project-overview.md) | Tujuan produk, persona, user journey, 4 modul FR, status FR-1.1 s/d FR-4.3 | Pertama kali. Sebelum menyentuh apa pun, untuk tahu *apa* yang dibangun. |
| [`architecture.md`](./architecture.md) | Arsitektur lokal satu proses, tech stack final, skema DB lengkap + indeks wajib | Sebelum menulis kode backend/worker atau menyentuh skema DB. |
| [`decisions.md`](./decisions.md) | ADR D1–D5 + keputusan turunan (T1–T9) | Sebelum mengusulkan perubahan desain. Kalau keputusan sudah ada di sini, jangan dibuka ulang tanpa alasan baru. |
| [`constraints.md`](./constraints.md) | Kondisi host hasil probe, aturan versi, batas paralel lokal, daftar FORBIDDEN | Sebelum menjalankan apa pun secara lokal. |
| [`metrics.md`](./metrics.md) | OKR PRD §1.3 vs metrik pengganti TECH_SPEC §0.1 + angka transkripsi CPU | Sebelum membuat klaim performa atau menyetel ekspektasi produk/UI. |
| [`technical-debt.md`](./technical-debt.md) | Tabel utang teknis TECH_SPEC §5.3 + catatan diarization/MediaPipe ASD | Saat memprioritaskan backlog atau menjawab "kenapa X belum ada?". |
| [`references.md`](./references.md) | Sumber eksternal terverifikasi + nilai tuning siap pakai | **Sebelum menulis kode Sprint 2–3** (reframing, subtitle ASS, parsing LLM). |

---

## Urutan Baca yang Disarankan (agent baru)

1. **`project-overview.md`** — pahami produk, persona, dan alur end-to-end.
2. **`constraints.md`** — pahami apa yang **tidak boleh** dilakukan (mis.
   menyalakan Postgres/Redis untuk dev lokal, menjalankan render di luar
   `clipper_shared.dispatcher`). File ini memuat koreksi environment
   terverifikasi yang mengalahkan TECH_SPEC §1.1.
3. **`decisions.md`** — pahami keputusan yang sudah dikunci (D1–D5, T1–T9)
   beserta konsekuensinya, supaya tidak mengusulkan ulang hal yang sudah ditolak.
4. **`architecture.md`** — pahami arsitektur lokal satu proses, tech stack, dan
   skema DB.
5. **`metrics.md`** — pahami bahwa target OKR kecepatan PRD **tidak tercapai**
   dan angka apa yang dipakai sebagai gantinya.
6. **`technical-debt.md`** — pahami utang teknis yang sudah dicatat agar tidak
   menganggapnya sebagai bug atau kelalaian.
7. **`references.md`** — sebelum menulis kode Sprint 2–3, baca nilai tuning yang
   sudah terverifikasi agar tidak meneliti ulang dari nol.

Setelah ketujuh file dibaca, dokumen sumbernya tetap otoritatif. Jika ada
perbedaan antara memori dan dokumen sumber, **dokumen sumber yang menang** —
lalu perbarui file memori yang relevan.

---

## Aturan Pemeliharaan

- Jangan mengarang detail yang tidak ada di PRD/TECH_SPEC. Tulis eksplisit
  **"BELUM DITENTUKAN"** bila ada celah.
- Setiap bagian baru harus mencantumkan rujukan bagian sumbernya.
- Metrik kecepatan **harus** konsisten dengan TECH_SPEC §0.1 (CPU-only):
  OKR "30 menit ≤ 5 menit" tidak tercapai.
- **D4 tidak lagi berlaku.** Keputusan "hanya dokumen, tanpa scaffold kode"
  sudah digantikan oleh instruksi pengguna berikutnya ("Lanjutkan proses
  development secara keseluruhan"). **Kode boleh dan memang sedang ditulis.**
  Jangan memakai D4 untuk menolak pekerjaan implementasi; jangan pula memakai
  file memori ini sebagai alasan untuk tidak menulis kode.
- `constraints.md` memuat koreksi lingkungan **terverifikasi** yang lebih akurat
  daripada TECH_SPEC §1.1. Bila keduanya berbenturan, **`constraints.md` yang
  menang** untuk fakta environment.

Sumber: TECH_SPEC §0, §0.1, §9; PRD §1, §3, §4.
