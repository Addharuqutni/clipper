# Dataset pengukuran kualitas pemilihan momen

Modul AI memilih momen dari **transkrip**, bukan dari gambar atau suara. Maka
yang diukur di sini adalah: **apakah momen yang dipilih AI cocok dengan momen
yang menurut manusia layak diklip**.

Tidak ada target angka resmi untuk pemilihan momen — PRD FR-2.2 mendefinisikan
tiga dimensi penilaian, tetapi tidak menetapkan ambang akurasi. Karena itu
aturan yang dipakai meniru reframing (TECH_SPEC §5.2):

> Ukur dulu pada **20 kasus berlabel**, baru klaim angkanya.

Alat ukurnya: [`scripts/eval_scoring.py`](../../scripts/eval_scoring.py).

---

## 1. Tata letak

```
eval/scoring/
├── README.md
└── cases/
    ├── contoh-kasus.json.example   ← contoh format
    └── <slug>.json                 ← satu berkas per video
```

Berkas `*.json` di `cases/` **ikut di-commit** — hanya berisi teks transkrip dan
angka, jadi kecil, dan justru itu buktinya. Berkas `*.json.example` dilewati
oleh pembaca dataset.

## 2. Format berkas kasus

```json
{
  "slug": "podcast-eps-12",
  "video_duration_s": 3180.0,
  "target_count": 5,
  "transcript": "[0.0s] selamat pagi semua ...\n[12.4s] jadi begini ...",
  "expected": [
    {"start_s": 412.0, "end_s": 455.0, "note": "debat seru soal harga rumah"},
    {"start_s": 1802.5, "end_s": 1849.0, "note": "pengakuan pribadi, emosi naik"}
  ]
}
```

| Field | Wajib | Keterangan |
|---|---|---|
| `slug` | tidak | Pengenal di laporan; bawaan = nama berkas |
| `transcript` | **ya** | Format baris `[12.4s] teks ...`, persis seperti yang dikirim ke model |
| `expected` | **ya** | Momen yang menurut Anda layak diklip |
| `target_count` | tidak | Berapa klip diminta dari sistem; bawaan 5 |
| `video_duration_s` | tidak | Dipakai memotong prediksi liar |

`transcript` disimpan dalam **format jadi**, bukan daftar kata, agar eval tidak
bergantung pada kode rendering transkrip — kalau kode itu berubah, dataset
lama tetap bisa dipakai.

## 3. Cara melabeli yang benar

1. **Ambil transkrip dari job yang sudah diproses**, jangan ketik ulang.
   Jalankan `scripts/bootstrap_eval_cases.py` untuk membuat draf kasus dari
   `output/clipper.db`, lalu isi bagian `expected`-nya.
2. **Tonton videonya.** Ini bagian yang tidak bisa digantikan. Tandai momen
   yang menurut Anda benar-benar layak diklip — yang kalau Anda lihat di
   TikTok, Anda tidak akan scroll lewat.
3. **Tulis `note` yang menjelaskan alasannya.** Catatan ini yang dipakai untuk
   mendiagnosis pola kegagalan nanti ("AI selalu melewat momen emosional").
4. **Jangan hanya melabeli momen yang mudah.** Kalau datasetnya berisi momen
   yang jelas-jelas bagus saja, angkanya menipu — sama seperti aturan
   reframing yang mewajibkan klip sulit ikut serta.
5. **Labeli apa adanya, bukan apa yang AI pilih.** Kalau Anda melabeli dari
   hasil AI, Anda mengukur AI terhadap dirinya sendiri.

## 4. Cara menjalankan

```cmd
set AI_API_KEY=...
set AI_MODEL=gemini-2.5-flash
.venv-win\Scripts\python.exe scripts\eval_scoring.py
.venv-win\Scripts\python.exe scripts\eval_scoring.py --json
.venv-win\Scripts\python.exe scripts\eval_scoring.py --case podcast-eps-12
```

Skrip memanggil **penyedia AI sungguhan**, jadi memakai API key dan kuota yang
sama dengan aplikasi.

Variabel lingkungan:

| Variabel | Bawaan |
|---|---|
| `AI_API_KEY` | wajib |
| `AI_MODEL` | wajib |
| `AI_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai` |
| `AI_PRESET` | `gemini` |
| `AI_ALLOW_LOCAL` | `false` |
| `AI_CONTEXT_TOKENS` | kosong |

## 5. Cara membaca hasilnya

- **Presisi** — dari klip yang diajukan, berapa yang benar-benar bagus.
  Rendah berarti banyak klip sampah.
- **Recall** — dari momen bagus yang ada, berapa yang ketemu.
  Rendah berarti AI melewat banyak momen bagus.
- **F1** — gabungan keduanya.
- **Kasus gagal** tidak masuk hitungan; mereka dilaporkan terpisah supaya
  gangguan provider tidak terbaca sebagai skor jelek.

Pencocokan memakai **IoU ≥ 0,5**: prediksi 40 detik dan label 40 detik boleh
bergantian bergeser paling banyak 20 detik dan tetap dihitung "momen yang sama".

Di bawah 20 kasus berlabel, skrip mencetak `!!! BELUM BOLEH DIKLAIM !!!` —
angka di atas hanya indikasi awal.
