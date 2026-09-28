# Dataset pengukuran presisi reframing

Target PRD **"presisi reframing ≥ 85%"** (PRD §1.3) adalah **target, bukan hasil**.
TECH_SPEC §5.2 menulis aturannya secara eksplisit:

> Ukur dulu pada **20 klip berlabel**, baru klaim angkanya.

Direktori ini adalah tempat pengukuran itu. Alat ukurnya:
[`scripts/eval_reframe.py`](../../scripts/eval_reframe.py).

---

## 1. Tata letak

```
eval/reframe/
├── README.md      ← berkas ini
├── labels.json    ← IKUT di-commit; kecil dan inilah buktinya
└── clips/         ← TIDAK di-commit (di-gitignore); berkas video berat
    ├── podcast-01.mp4
    └── ...
```

Simpan berkas klip di `eval/reframe/clips/`. Di `labels.json`, kolom `clip`
boleh diisi nama berkas saja (`podcast-01.mp4`, dicari di `clips/`) atau jalur
relatif/absolut ke lokasi lain.

## 2. Cara melabeli (yang benar-benar dilakukan)

1. Simpan calon klip di `eval/reframe/clips/`. Pakai klip yang **sama seperti
   yang dilihat pengguna**: wawancara/podcast dengan subjek bergerak, termasuk
   yang sulit (dua wajah, subjek keluar-masuk frame). Klip mudah saja membuat
   angkanya menipu.
2. Buka video (VLC / pemutar apa pun). **Pause** di momen ketika Anda tahu siapa
   pembicara aktifnya — mis. saat subjek jelas sedang bicara.
3. **Baca posisi x wajah itu**: dalam pemutar apa pun, ambil koordinat x titik
   tengah wajah (kiri–kanan), lalu bagi dengan lebar video. Contoh: wajah di
   x = 480 pada video selebar 1920 → `cx = 0.25`.
   - Cara cepat tanpa koordinat: pakai penggaris di layar (atau perkiraan
     pecahan: tepi kiri = 0,00; seperempat = 0,25; tengah = 0,50; dst.).
   - Kalau ragu, ambil waktu `t` yang mudah (misal tepat di 2,0 detik) dan
     `cx` perkiraan terbaik — label yang jelas salah justru berguna: itu
     menemukan klip yang reframer-nya memang lemah.
4. Catat **beberapa keyframe per klip** (minimal 3–5, lebih baik 10–20), sebarkan
   di sepanjang durasi. Satu keyframe per klip membuat angkanya goyang.
5. Tulis satu entri per klip ke `labels.json`:

```json
[
  {
    "clip": "podcast-01.mp4",
    "keyframes": [
      { "t": 2.0, "cx": 0.25 },
      { "t": 7.5, "cx": 0.25 },
      { "t": 14.0, "cx": 0.72 }
    ]
  }
]
```

Aturan skema:

| Kolom | Arti | Batasan |
|---|---|---|
| `clip` | Nama berkas klip | di `clips/`, relatif dataset, atau jalur absolut |
| `keyframes[].t` | Detik dari **awal klip** | ≥ 0; harus < durasi klip |
| `keyframes[].cx` | Titik tengah horizontal wajah **pembicara aktif** | 0,0 (tepi kiri) … 1,0 (tepi kanan) |

`labels.json` **wajib** ikut di-commit: itu bukti dari angka yang dilaporkan.
Media di `clips/` **jangan** di-commit (ratusan MB per berkas; sudah ada di
`.gitignore`). Contoh bentuk berkasnya: `labels.json.example` — salin menjadi
`labels.json` lalu ganti isinya dengan klip Anda.

## 3. Menjalankan pengukuran

```cmd
.venv-win\Scripts\python.exe scripts\eval_reframe.py
```

Opsi:

| Opsi | Arti |
|---|---|
| `--json` | keluaran mesin (JSON ke stdout) untuk dicatat/disimpan |
| `--margin 0.1` | ubah margin HIT bermargin (bawaan 0,2 → wajah harus di dalam 80% tengah crop) |
| `--dataset <dir>` | direktori dataset lain (bawaan `eval/reframe`) |
| `--clips a.mp4 b.mp4` | batasi ke beberapa klip |
| `--model <file>` | jalur model Face Landmarker (bawaan dari `.env`) |
| `--verbose` | log DEBUG (termasuk peringatan pelacakan dari reframer) |

Skrip memanggil fungsi yang **sama** dengan render sungguhan
(`worker_render.reframer.compute_crop_positions`) tetapi berhenti sebelum
tahap encode — tidak ada berkas video yang ditulis. Kode keluar **0** selalu,
kecuali error nyata (label hilang, klip gagal dibaca). Presisi rendah bukan error.

## 4. Definisi metrik

Untuk tiap keyframe `(t, cx)`:

1. Ambil posisi crop `x` yang berlaku pada frame ke-`floor(t × fps)` dari
   lintasan hasil reframer (persis jendela yang dipotong render).
2. Lebar crop = `crop_width_for(width, height)` — untuk sumber 16:9, 1080/1920
   dari tinggi penuh (lihat `clipper_shared.reframe`).
3. **HIT** bila `cx × lebar_sumber` berada **di dalam** `[x, x + lebar_crop]`
   (batas tepi dihitung HIT).
4. **HIT bermargin (80%)** bila wajah berada di dalam **80% tengah** jendela
   crop — yaitu 10% pertama dan 10% terakhir tidak dihitung. Ini menangkap
   kasus yang secara teknis "masih terlihat" tetapi wajahnya mepet potong.

Angka yang dilaporkan:

```
presisi = jumlah keyframe HIT / jumlah keyframe yang dinilai
```

Agregasi memakai **total keyframe**, bukan rata-rata antar klip: klip dengan
1 keyframe tidak boleh berbobot sama dengan klip 20 keyframe.

Kasus khusus yang sudah ditangani skrip:

- **Tidak ada wajah sama sekali di satu klip** — reframer memakai crop tengah
  statis; penilaian memakai lintasan yang sama agar mencerminkan hasil nyata.
- **Keyframe di luar durasi klip** — dihitung MISS dan diperingatkan; labelnya
  yang salah, bukan pelacakannya.

Bila klip < 20, skrip mencetak peringatan besar **"BELUM BOLEH DIKLAIM"**.
Jangan mengutip angkanya di dokumen/UI/laporan sebelum peringatan itu hilang
(Sprint 3 butir 31).

## 5. Melaporkan hasil

Setelah ≥ 20 klip berlabel dan diukur, catat ke `docs/memory/metrics.md`:

- tanggal pengukuran dan jumlah klip/keyframe;
- presisi (dan presisi bermargin);
- versi kode (commit) yang diukur;
- hasil `--json` mentahnya bila perlu.

Sebelum itu, di dokumen lain statusnya tetap **BELUM DITENTUKAN**.
