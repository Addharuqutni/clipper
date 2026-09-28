# Pemakaian

## Alur kerja

```
Upload / YouTube
      │
      ▼
   ingest ──────── unduh media, ambil subtitle bila tersedia
      │            (Whisper dilewati bila video punya subtitle sendiri)
      ▼
 transcribe ───── faster-whisper di CPU, hanya bila subtitle tidak ada
      │
      ▼
   analyze ────── AI memilih momen terbaik dari teks transkrip
      │
      ▼
   render ─────── FFmpeg + MediaPipe → klip 9:16 dengan subtitle
      │
      ▼
  review & unduh ─ ubah transkrip, segmen, caption, overlay, crop; unduh MP4
```

## 1. Isi penyedia AI

Buka **Setelan** (`/settings`). Tanpa pengaturan yang lengkap, video ditolak
**saat dikirim** dengan pesan `Penyedia AI belum siap: ...` — sebelum diunduh,
bukan setelah menunggu transkripsi.

| Field | Keterangan |
|---|---|
| Preset | Pilih penyedia (Gemini, OpenAI, Groq, OpenRouter, Ollama) atau `Kustom (kompatibel OpenAI)`. |
| URL dasar | Endpoint API, mis. `http://localhost:20128/v1`. Terisi otomatis untuk preset. |
| Model | Nama model, mis. `gemini-2.5-flash`. |
| API key | **Wajib** kecuali preset `Ollama (lokal)`. Disimpan terenkripsi dan tidak pernah ditampilkan lagi. |
| Izinkan alamat lokal | **Wajib dicentang** bila URL menunjuk `localhost` atau jaringan lokal. |
| Konteks model | Biarkan kosong: dideteksi otomatis dari penyedia saat Simpan. Isi hanya bila penyedia tidak melaporkannya. |

Tekan **Simpan**, lalu **Uji koneksi**. Pesannya membedakan kunci ditolak
(401), model tidak ada (404), dan penyedia sedang gangguan (5xx).

Panel kanan menampilkan konteks model dan **durasi video maksimum** yang
berlaku untuk model itu.

## 2. Kirim video

- **YouTube:** tempel tautan `watch?v=...`, `youtu.be/...`, atau
  `shorts/...`. Video harus publik atau unlisted. Untuk video yang butuh login
  atau dibatasi usia, unggah `cookies.txt` di bagian bawah halaman — cookies
  disimpan terenkripsi dan dipakai untuk semua unduhan berikutnya sampai
  dihapus.
- **Upload:** seret berkas `.mp4`, `.mov`, atau `.mkv` (maks 3 GB). Bila
  dijeda, tab ditutup, atau aplikasi dimulai ulang, pilih berkas yang sama
  lalu tekan Upload lagi: unggahan dilanjutkan dari bagian terakhir.

**Jumlah klip:** pakai tombol `−`/`+`, ketik angkanya, atau pilih cepat
3 / 5 / 10 / 20 / 30. Tiap klip berdurasi 30–60 detik.

## 3. Batas

| Batas | Nilai | Keterangan |
|---|---|---|
| Durasi video | Mengikuti konteks model AI, maks **180 menit** | Rumus: (konteks − 6.144 token) ÷ 400 token/menit. Batas atas diubah lewat `MAX_VIDEO_DURATION_MIN` di `.env`. Video YouTube yang lebih panjang ditolak sebelum diunduh. |
| Jumlah klip | 1–30, maks **1 klip per 3 menit** video | Video 15 menit → maks 5, 60 menit → maks 20, 90 menit ke atas → 30. |
| Ukuran berkas upload | 3 GB | |

Soal jumlah klip: halaman Upload membaca durasi berkas di browser dan langsung
membatasi pilihan. Untuk YouTube, durasi baru diketahui saat diproses; bila
permintaan melebihi batas, jumlahnya dipangkas saat analisis dan tercatat di
log job, mis. `diminta 20; video 12 menit maks 4 klip`.

**Kecepatan:** transkripsi di CPU kira-kira sama dengan durasi video (video 30
menit ≈ 30–60 menit). Bila video YouTube punya subtitle sendiri, transkripsi
dilewati dan prosesnya jauh lebih cepat.

## 4. Halaman job

Setelah dikirim, halaman job menampilkan progres langsung. Tombol di kanan
atas:

| Tombol | Kapan | Fungsi |
|---|---|---|
| Batalkan | Job sedang diproses | Tahap berikutnya tidak dijalankan. |
| Proses ulang | Job gagal/dibatalkan/selesai | Mengulang dari unduhan/ingest. |
| Analisis ulang | Job selesai atau gagal setelah transkripsi | Meminta AI memilih ulang momen memakai transkrip yang sudah ada (tanpa unduh/transkripsi ulang). |
| Hapus | Kapan saja | Menghapus job, video sumber, dan hasil render. Salinan di `output\clips\` tetap ada. |

Per klip: **Render ulang** (memakai mode crop dan gaya di tab Tampilan),
**Ubah rentang** (potong awal/akhir klip), **B-roll dan efek suara**, dan
**Caption unggahan** — AI menulis caption + hashtag siap tempel.

Bila penyedia AI gagal saat analisis, aplikasi tetap membuat klip dari bagian
dengan bicara paling padat dan menuliskan alasannya di log. Tekan
**Analisis ulang** setelah penyedia pulih.

## 5. Hasil klip

Klip jadi ditulis langsung ke **`output\clips\`** dengan nama deskriptif:

```
output\clips\4d6d1fb7_ec596c52_01_mongol-bongkar-kejanggalan_final.mp4
```

| Bagian | Arti |
|---|---|
| `4d6d1fb7` | 8 karakter pertama ID job |
| `ec596c52` | 8 karakter pertama ID segmen |
| `01` | Urutan segmen, mengikuti waktu mulai di video |
| `mongol-bongkar-...` | Label segmen dari AI |
| `final` / `preview` | `final` 1080×1920 Full HD (bawaan, untuk media sosial); `preview` 540×960 |

Isi folder `output\`:

```
output\
  clipper.db                    basis data (job, segmen, pengaturan AI)
  clips\                        klip dengan nama deskriptif — aman dihapus
  clipper-renders\              salinan kanonik yang dirujuk basis data — JANGAN dihapus
  clipper-raw\                  video sumber (~1–2 GB per jam video)
  clipper-overlays\             aset overlay/B-roll
  clipper-fonts\                font kustom
  secrets\                      cookies YouTube terenkripsi
```

Berkas di `clips\` adalah *hard link* ke `clipper-renders\` (satu berkas
fisik, dua nama), jadi tidak memakan ruang dua kali. Menghapus salah satunya
aman; menghapus `clipper-renders\` membuat halaman job kehilangan pemutarnya.

**Retensi:** video sumber di `clipper-raw\` dihapus otomatis **48 jam setelah
render terakhir job selesai** (`RAW_MEDIA_TTL_HOURS`). Setelah itu klip yang
sudah jadi tetap ada, tetapi render ulang butuh job baru.

## 6. Mengosongkan data

Hentikan aplikasi, lalu hapus basis data dan media. **Pengaturan penyedia AI
ikut terhapus**, jadi isi ulang di `/settings`.

```cmd
stop.cmd
del output\clipper.db
rmdir /s /q output\clipper-raw output\clipper-renders output\clipper-overlays output\clips output\uploads .work
```

Basis data baru dibuat otomatis saat aplikasi dinyalakan lagi.
