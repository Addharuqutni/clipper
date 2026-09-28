# Technical Debt — Utang Teknis Resmi

Utang teknis yang **sudah dicatat dan diterima**, bukan bug dan bukan kelalaian.
Jangan mengangkatnya sebagai temuan baru; angkat hanya kalau pemicu peninjauannya
sudah tercapai.

Sumber: TECH_SPEC §5.2, §5.3.

---

## 1. Tabel Utang Teknis (TECH_SPEC §5.3)

| Utang | Alasan ditunda | Kapan ditinjau |
|---|---|---|
| **Speaker diarization** (`pyannote`) | Terlalu berat di CPU | Saat GPU tersedia atau STT pindah ke API |
| **Auto-scale / deployment server** | Aplikasi lokal saja (**T9**) | Bila perlu multi-pengguna di server |
| **Overlay B-roll = encode kedua** | Subtitle sudah satu pass dengan reframe; overlay butuh `filter_complex` dengan input tambahan | Bila render dengan overlay terasa lambat |
| **Batal tidak memutus FFmpeg/Whisper yang berjalan** | Berhenti di titik periksa `emit`; proses anak dibiarkan selesai | Bila pembatalan cepat penting |
| **Kandidat cadangan tanpa AI** = kepadatan kata | Heuristik murah saat penyedia AI gagal | Bila kualitas cadangan dikeluhkan |
| **Akurasi `large-v3` penuh** | Kecepatan CPU | Saat GPU tersedia |

**Implikasi terhadap FR:**

| Utang | FR terdampak |
|---|---|
| Speaker diarization | **FR-2.1** (Timestamped Transcription & **Diarization**) — bagian diarization tidak dipenuhi |
| Auto-scale / deployment server | PRD §5 NFR (*Scalability*) |
| Akurasi `large-v3` penuh | Akurasi transkripsi keseluruhan; default MVP tetap `small` |

Sumber: PRD §4, §5; TECH_SPEC §5.3.

---

## 2. Catatan Diarization Ditunda

Diarization (pemisahan pembicara) **ditunda** dan `pyannote` dinilai terlalu
berat untuk CPU.

**Yang dipakai sebagai gantinya di MVP** (heuristik, bukan diarization sungguhan):

- heuristik **energi audio**, dan
- **gap** (jeda) antar ucapan.

Konsekuensi: kolom `transcripts.speakers` (JSONB) tetap ada di skema, tetapi
isinya pada MVP **BELUM DITENTUKAN** tingkat keandalannya — hanya heuristik.

Sumber: TECH_SPEC §2 (baris Diarization), §3, §5.3.

---

## 3. Catatan MediaPipe Tidak Punya Active-Speaker Detection

**Pernyataan kunci:** MediaPipe **tidak menyediakan active-speaker detection
(ASD)**, padahal **PRD §6 mengasumsikan** kemampuan ini tersedia (baris "Video
Engine: FFmpeg, OpenCV, MediaPipe — MediaPipe untuk tracking wajah & reframe").

### Jalur MVP pengganti (TECH_SPEC §5.2)

1. Deteksi wajah **BlazeFace** pada **5 fps** — sampling, bukan tiap frame.
2. **ByteTrack** (`boxmot`) untuk identitas wajah stabil antar frame.
3. **Skor pembicara aktif** = f(perubahan area mulut, energi audio pada window,
   kontinuitas track).
4. **Crop:** median filter window **15 frame** + **deadband** — kamera tidak
   bergerak bila pergeseran < **3%** lebar frame (mencegah kamera goyang).
5. **Split-screen** otomatis hanya bila ≥ 2 track aktif dengan pergantian bicara cepat.

### Pernyataan jujur dari spec

> Target 85% akan **sulit** pada rekaman dengan banyak wajah kecil atau subjek di
> luar frame. **Ukur dulu pada 20 klip berlabel, baru klaim angkanya.**

Karena itu **presisi reframing ≥ 85% adalah target, bukan hasil terverifikasi**
(lihat `metrics.md`). Klaim tanpa pengukuran dilarang (Sprint 3 butir 31).

Sumber: PRD §6; TECH_SPEC §5.2, §6 Sprint 3 butir 27 & 31.

---

## 4. Ringkasan Risiko Utang yang Sudah Diakui

TECH_SPEC §7 mencatat empat risiko yang terkait langsung dengan utang di atas:

| Risiko | Mitigasi |
|---|---|
| CPU starvation di VPS (akibat D3) | §4.2 + §4.3 + uji butir 39 |
| Backup tidak teruji (akibat D3) | Butir 41: restore drill wajib |
| Target OKR kecepatan tidak tercapai (akibat D1) | §0.1: metrik diganti + diukur di butir 19/42 |

Sumber: TECH_SPEC §7.

---

## 5. BELUM DITENTUKAN

- **BELUM DITENTUKAN** — Kapan tepatnya GPU tersedia (semua tiga utang yang
  menunggu GPU memakai pemicu yang sama, tapi tidak ada tanggal/anggaran).
- **BELUM DITENTUKAN** — Pengganti konkret untuk diarization jangka panjang
  (mis. model apa saat GPU tersedia).
- **BELUM DITENTUKAN** — Mekanisme auto-scale saat migrasi ke cloud (disebut
  sebagai pemicu peninjauan, bukan rencana).
- **BELUM DITENTUKAN** — Tingkat keandalan heuristik energi+gap vs diarization
  sungguhan (belum ada pengukuran).

Sumber: TECH_SPEC §5.3, §7, §8.
