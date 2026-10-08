# Korpus klip viral — distilasi pola menjadi rubrik

Mekanisme 3 dari tiga cara "belajar dari klip viral": **bukan** fine-tuning,
**bukan** few-shot. Korpus ini dibaca, pola teksnya diekstrak, lalu ditulis
menjadi **draf rubrik** untuk ditinjau manusia.

Alatnya: [`scripts/distill_viral_patterns.py`](../../scripts/distill_viral_patterns.py).

---

## Batas yang harus dipahami dulu

Agent memilih momen **hanya dari transkrip**. Tidak melihat gambar, tidak
mendengar suara. Sementara yang membuat klip viral sebagian besar ada di LUAR
transkrip:

| Bisa dipelajari dari teks | TIDAK bisa dipelajari dari teks |
|---|---|
| Pola kalimat pembuka | Hook visual (wajah, teks di layar) |
| Apakah gagasan selesai | Nada bicara, musik, jeda dramatis |
| Kepadatan dan jeda bicara | Algoritma, waktu posting |
| | Jumlah follower yang sudah dimiliki channel |

Jadi skrip ini tidak pernah mengklaim "belajar virality". Ia mengekstrak
**pola teks yang bisa ditiru**. Klaim lebih dari itu harus diukur dengan
`eval_scoring.py`, dan sampai sekarang belum terukur.

---

## Aturan korpus yang menentukan benar atau menyesatkan

Ini bagian yang paling sering salah. Dua aturan:

### 1. Satuannya harus KLIP, bukan video utuh

Melabel video 60 menit sebagai "viral" **tidak memberi tahu model momen MANA
yang bagus**. Yang masuk korpus adalah potongan pendek — klip yang benar-benar
berdiri sendiri.

### 2. Label jangan view count mentah

View count mentah terkontaminasi ukuran channel. Video dari channel besar lebih
sering viral, dan kalau itu yang masuk korpus, model belajar mengenali **gaya
bicara channel besar**, bukan momen yang bagus. Itu bukan kecerdasan — itu
menghafal siapa yang sudah terkenal.

Pakai **rasio terhadap median channel itu sendiri**: video yang mengalahkan
median channel-nya beberapa kali lipat, bukan video dengan view count absolut
tertinggi.

---

## Sumber data per platform

| Platform | Bisa? | Catatan |
|---|---|---|
| **YouTube** | Ya | Data API v3: `search.list` + `videos.list` (`viewCount`, `likeCount`). `videos.list` 1 unit kuota; `search.list` **batas 100 panggilan/hari**. |
| **TikTok** | Belum terverifikasi | Research API untuk akademisi/non-profit dan melarang produk komersial; Display API untuk menampilkan konten beratribusi, bukan analitik massal. **Cek dokumen resminya sendiri** sebelum dipakai. |
| **Instagram / Facebook** | Pada praktiknya tidak | Graph API hanya memberi insights untuk akun yang Anda miliki sendiri. Virality konten publik orang lain tidak tersedia lewat API resmi. |

**Jangan scraping.** Selain melanggar ToS, datanya tidak stabil dan tidak bisa
dipertanggungjawabkan.

Transkripnya sendiri bisa diambil lewat jalur yang sudah ada di repo — yt-dlp
dan dukungan cookies sudah terpasang (`media_fetcher.py`, `youtube_cookies.py`),
dan basis data menunjukkan subtitle YouTube sudah dipakai
(`model_used = 'youtube_subtitle'`).

---

## Tata letak

```
eval/viral/
├── README.md
├── rubrik-usulan.md     ← keluaran skrip (DRAF)
└── clips/
    ├── contoh-klip-01.json.example
    └── <slug>.json
```

Format berkas klip:

```json
{
  "slug": "nama-klip",
  "note": "topik atau alasan masuk korpus",
  "transcript": "teks transkrip klip, boleh bertimestamp"
}
```

Berkas `*.json.example` dilewati pembaca korpus.

## Cara menjalankan

```cmd
.venv-win\Scripts\python.exe scripts\distill_viral_patterns.py --dry-run
set AI_API_KEY=...
set AI_MODEL=gemini-2.5-flash
.venv-win\Scripts\python.exe scripts\distill_viral_patterns.py
```

`--dry-run` menampilkan prompt dan jumlah karakter tanpa memanggil penyedia —
pakai ini dulu untuk memeriksa biaya sebelum benar-benar menjalankan.

## Keluaran

Draf markdown di `rubrik-usulan.md`, berisi tiga bagian:

- **Pola yang berulang** — hanya yang muncul di lebih dari satu klip, lengkap
  dengan bukti kutipan nyata dan tindakan yang bisa dilakukan
- **Pola yang justru dihindari**
- **Catatan** — termasuk pola yang TIDAK bisa ditiru dari teks

**Draf ini tidak mengubah apa pun di produksi.** Untuk menerapkan, pindahkan
pola yang relevan ke `clipper_shared.scoring_prompt`, lalu ukur dengan
`eval_scoring.py`. Tanpa pengukuran itu, tidak ada bukti pola ini membantu —
hanya menambah panjang prompt.
