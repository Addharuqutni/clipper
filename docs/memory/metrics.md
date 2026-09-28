# Metrics — OKR vs Metrik Pengganti

> ⚠️ **Baca ini dulu:** OKR PRD §1.3 **"Processing 30 menit ≤ 5 menit" TIDAK
> TERCAPAI** pada jalur CPU-only (keputusan **D1**). Jangan pernah menuliskan
> target ini seolah tercapai — baik di UI, marketing copy, maupun laporan.

Sumber: PRD §1.3; TECH_SPEC §0.1.

---

## 1. Tabel Perbandingan OKR PRD vs Metrik Pengganti

| Metrik PRD §1.3 | Status setelah D1 | Metrik Pengganti (TECH_SPEC §0.1) |
|---|---|---|
| **Processing Speed:** video 30 menit selesai ≤ 5 menit | ❌ **Tidak realistis di CPU** | **Time-to-first-clip ≤ 25 menit** (video 30 menit, model `small`/int8, 4 vCPU). End-to-end penuh **~50–90 menit**. |
| **Reframing Accuracy:** presisi *active speaker detection* ≥ 85% | ⚠️ **Bergantung heuristik** | Tetap **≥ 85%**, tapi diukur pada set uji berlabel **20 klip**. Lihat §5.2 TECH_SPEC. **Jangan klaim 85% sebelum diukur.** |
| **Export & Publish Rate:** ≥ 65% klip rekomendasi AI diekspor/dipublikasikan | ✅ Tetap | Tetap; diukur pada klip yang direkomendasikan AI dan **diunduh** (publishing dihapus oleh D5). |
| **Upload Reliability:** ≥ 98% untuk berkas > 1 GB | ✅ Tetap | Tetap, dengan **multipart resume**. |

**Catatan penting per baris:**

- **Processing Speed** — satu-satunya metrik yang benar-benar dibatalkan. Produk
  harus di-set ekspektasinya ke *time-to-first-clip*, bukan end-to-end.
- **Reframing Accuracy** — bukan dibatalkan, tapi **belum terverifikasi**.
  MediaPipe tidak punya active-speaker detection (lihat `technical-debt.md`),
  jadi angka 85% adalah target, bukan hasil. Sprint 3 butir 31 secara eksplisit
  meminta kalibrasi dan pelaporan presisi aktual sebelum mengklaim.
  **Alat ukurnya sudah ada** (lihat §1.1): `scripts/eval_reframe.py` +
  dataset `eval/reframe/`. Yang belum ada hanyalah **20 klip berlabelnya** —
  sampai itu terisi, statusnya tetap BELUM DITENTUKAN.
- **Export & Publish Rate** — kini murni Export Rate: publishing dihapus (D5),
  jadi pengukuran bergantung pada unduhan.

Sumber: PRD §1.3; TECH_SPEC §0.1, §5.2, §6 Sprint 3 butir 31.

---

## 1.1 Cara Mengukur Presisi Reframing (harness)

Alat ukur sudah tersedia; yang belum ada adalah **datanya** (20 klip berlabel).

| Bagian | Lokasi |
|---|---|
| Dataset + tata cara melabeli | `eval/reframe/README.md` (Indonesia) |
| Label klip | `eval/reframe/labels.json` (ikut di-commit; media di `eval/reframe/clips/` di-gitignore) |
| Skrip pengukur | `scripts/eval_reframe.py` |
| Uji logika penilaian | `apps/worker-render/tests/test_reframe_eval.py` |
| Fungsi murni HIT/MISS + agregasi | `apps/worker-render/worker_render/reframe_eval.py` |

Perintah (dari akar repo):

```cmd
.venv-win\Scripts\python.exe scripts\eval_reframe.py
.venv-win\Scripts\python.exe scripts\eval_reframe.py --json
```

Skrip memanggil `worker_render.reframer.compute_crop_positions` — fungsi yang
sama dengan render sungguhan — tetapi berhenti sebelum encode, jadi tidak ada
video yang ditulis. Definisi metrikl: satu **HIT** bila titik tengah wajah yang
dilabeli berada di dalam jendela crop 9:16 pada frame tersebut (batas tepi
dihitung HIT); **presisi** = total HIT / total keyframe, diagregasi lintas klip.
Dilaporkan juga HIT bermargin (bawaan: wajah di dalam **80% tengah** crop).
Kode keluar 0 selalu kecuali error nyata.

**Gerbang klaim.** Bila klip berlabel < 20, skrip mencetak peringatan besar
**"BELUM BOLEH DIKLAIM"** (TECH_SPEC §5.2). Sebelum 20 klip terlabeli dan
diukur, baris Reframing Accuracy di atas **tetap BELUM DITENTUKAN** dan angka
85% tidak boleh dikutip sebagai hasil — baik di UI, dokumentasi, maupun laporan.
Setelah pengukuran, hasilnya dicatat di §5 dokumen ini beserta tanggal, jumlah
klip/keyframe, dan commit yang diukur.

Sumber: PRD §1.3; TECH_SPEC §5.2, §6 Sprint 3 butir 31; `docs/memory/technical-debt.md` §3.

---

## 2. Angka Waktu Transkripsi CPU (faster-whisper, int8)

Ekspektasi realistis untuk perencanaan. Semua angka **bergantung jumlah vCPU**.

| Model | Kecepatan relatif (× realtime) | Video 30 menit → estimasi durasi transkripsi |
|---|---|---|
| **`small`** (default MVP) | ≈ **0.5–0.9×** realtime | ~33–60 menit |
| **`medium`** | ≈ **0.25–0.5×** realtime | ~60–120 menit |
| **`large-v3-turbo`** | ≈ **0.4–0.7×** realtime | ~43–75 menit |

> Interpretasi: arti "0.5× realtime" adalah transkripsi berjalan pada setengah
> kecepatan waktu nyata — video 30 menit butuh ~60 menit untuk ditranskripsi.
> **Semua** nilai di tabel ini jauh melampaui target PRD 5 menit.

**Default MVP:** `small` + int8. Ini pilihan sadar untuk meminimalkan
*time-to-first-clip* pada hardware CPU tanpa GPU.

Model di-cache di volume persisten agar tidak diunduh ulang per job.

Sumber: TECH_SPEC §0.1, §2.

---

## 3. Bagaimana Angka Ini Dipakai

| Konteks | Angka yang dipakai |
|---|---|
| Klaim pemasaran / copy UI | **Time-to-first-clip ≤ 25 menit**, bukan "5 menit" |
| Perencanaan kapasitas `RENDER_SLOTS`/`STT_SLOTS` | Tabel §2 di atas + kapasitas host lokal (`constraints.md` §6; BELUM DITENTUKAN) |
| Validasi akhir | Sprint 5 butir 42: **laporkan time-to-first-clip aktual** memakai kerangka §0.1 |
| Penetapan default model | `small` int8 |
| Presisi reframing | Diukur di Sprint 3 butir 31 pada set uji 20 klip berlabel — harness: `scripts/eval_reframe.py` (§1.1) |

Sprint 5 butir 38 juga mewajibkan pengukuran **p95** dan **biaya per video**
melalui stress test (20 upload paralel @ 2 GB, 10 job render bersamaan).

Sumber: TECH_SPEC §0.1, §6 Sprint 3 butir 31, Sprint 5 butir 38, 42.

---

## 4. Risiko Metrik yang Sudah Dicatat

TECH_SPEC §7 mencatat "**Target OKR kecepatan tidak tercapai** (risiko baru
akibat D1)" dengan dampak "ekspektasi produk meleset" dan mitigasi "§0.1: metrik
diganti + diukur di butir 19/42".

Sumber: TECH_SPEC §7, §6 Sprint 5 butir 42.

---

## 5. BELUM DITENTUKAN

- **BELUM DITENTUKAN** — Nilai `time-to-first-clip` aktual di host ini
  (baru akan diukur Sprint 5 butir 42; angka 25 menit adalah **target**, bukan hasil).
- **BELUM DITENTUKAN** — Presisi reframing aktual (target ≥ 85%; hasil kalibrasi
  Sprint 3 butir 31 belum ada). **Alat ukur sudah siap** (§1.1:
  `scripts/eval_reframe.py` + `eval/reframe/`); yang belum ada adalah **20 klip
  berlabel** yang harus diisi pemilik produk. Sampai itu terjadi, angka apa pun
  dari skrip tersebut **belum boleh diklaim** (peringatan itu dicetak skripnya
  sendiri).
- **BELUM DITENTUKAN** — Kapasitas final host → semua angka tabel §2 bersifat
  indikatif sampai `RENDER_SLOTS`/`STT_SLOTS` diukur (`constraints.md` §6).
- **BELUM DITENTUKAN** — Biaya per video (target pengukuran Sprint 5 butir 38).

Sumber: TECH_SPEC §0.1, §6 Sprint 3 butir 31, Sprint 5 butir 38 & 42; T9.
