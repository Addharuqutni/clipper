# Panduan pelabelan — kasus viral

9 kasus di `eval/scoring/cases/viral-*.json` diambil dari klip YouTube yang
**benar-benar viral** (1,2 juta – 18,6 juta views). Ini bukan karangan.

Tujuannya: mengisi daftar `expected` — momen yang menurut **Anda** layak
dijadikan klip. Nanti `eval_scoring.py` membandingkan pilihan AI dengan
pilihan Anda.

---

## Cara melabeli

Buka berkas, isi bagian `expected`:

```json
"expected": [
  {
    "start_s": 0.0,
    "end_s": 57.0,
    "note": "momen yang layak diklip, dan alasannya"
  }
]
```

**Aturan:**

1. **Satu kasus, satu momen.** `target_count` di semua kasus viral adalah 1 —
   jadi isi tepat satu entri. Ini membuat penilaian lebih tegas.
2. **Batasannya boleh kasar.** Yang penting momennya tercakup; IoU 0,5 sudah
   dianggap "momen yang sama".
3. **`note` wajib.** Tulis alasannya — catatan inilah yang dipakai untuk
   mendiagnosis pola kegagalan nanti.
4. **Labeli apa adanya, bukan apa yang AI pilih.** Kalau Anda melabeli dari
   hasil AI, Anda mengukur AI terhadap dirinya sendiri.

---

## Yang perlu diperhatikan saat menilai

Transkrip ini **caption otomatis**, jadi:
- Kadang salah dengar (contoh: klip `jFMuXEs4evv` isinya nyaris tidak bisa
  dibaca — labeli seadanya atau tandai di `note`)
- Tidak ada tanda baca
- Tawa dan reaksi ditandai `[tertawa]`, `[berteriak]`

**Yang tidak bisa Anda lihat:** gambar, nada bicara, ekspresi wajah. Itu
kekurangan nyata dari pelabelan berbasis transkrip — catat di `note` bila
Anda merasa butuh konteks visual untuk menilai.

---

## Contoh pengisian

```json
"expected": [
  {
    "start_s": 0.0,
    "end_s": 57.0,
    "note": "Seluruh klip: tantangan 'sebutin artis yang pernah dipacarin' sudah lengkap dari awal sampai punchline 'Renata Muluk'"
  }
]
```

---

## Setelah selesai

```cmd
set AI_API_KEY=<key>
set AI_MODEL=antigravity/gemini-3.8-flash
set AI_BASE_URL=http://127.0.0.1:5173/v1
set AI_ALLOW_LOCAL=true
.venv-win\Scripts\python.exe scripts\eval_scoring.py
```

Dibutuhkan **20 kasus berlabel** sebelum angka boleh diklaim. 9 kasus viral
ini adalah awal — sisanya bisa diambil dari job yang sudah diproses lewat
`scripts/bootstrap_eval_cases.py`.
