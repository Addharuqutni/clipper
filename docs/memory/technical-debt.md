# Technical Debt — Utang Teknis Resmi

Utang teknis yang **sudah dicatat dan diterima**, bukan bug dan bukan kelalaian.
Jangan mengangkatnya sebagai temuan baru; angkat hanya kalau pemicu peninjauannya
sudah tercapai.

Sumber: TECH_SPEC §5.2, §5.3; T9.

---

## 1. Tabel Utang Teknis (TECH_SPEC §5.3)

| Utang | Alasan ditunda | Kapan ditinjau |
|---|---|---|
| **Speaker diarization** (`pyannote`) | Terlalu berat di CPU | Saat GPU tersedia atau STT pindah ke API |
| **Auto-scale / deployment server** | Aplikasi lokal saja (**T9**); tidak ada VPS/container | Bila perlu multi-pengguna di server (berarti menulis ulang lapisan antrean/penyimpanan) |
| **Overlay B-roll = encode kedua** | Subtitle sudah satu pass dengan reframe; overlay butuh `filter_complex` dengan input tambahan | Bila render dengan overlay terasa lambat |
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

### Jalur MVP pengganti (TECH_SPEC §5.2 revisi)

1. **MediaPipe Face Landmarker** (`mediapipe.tasks`, `RunningMode.VIDEO`,
   `num_faces=10`) dengan konversi **BGR→RGB** sebelum `detect_for_video()`.
2. **Sinyal bicara** dari bukaan 24 lip-landmark: `MIN_SPEAK_RATIO=0.18`.
3. **Skor pembicara aktif** per frame: ukuran × (1 + `SPEAKER_BONUS`×bicara) −
   `CONTINUITY_WEIGHT`×jarak dari crop sekarang.
4. **Penghalusan crop:** `EMA_ALPHA=0.15`, deadband `DEADZONE_FRAC=0.02`,
   snap pada ganti adegan `SNAP_FRAC=0.35`; pertahankan posisi terakhir bila
   wajah hilang.
5. **`ByteTrack`/`boxmot` dibatalkan** — kontinuitas dijaga oleh
   `CONTINUITY_WEIGHT`, bukan tracker terpisah.

Rincian lengkap dan kedua jebakan implementasinya (ruang warna; deadlock pipe
FFmpeg) ada di TECH_SPEC §5.2.

### Pernyataan jujur dari spec

> Target 85% akan **sulit** pada rekaman dengan banyak wajah kecil atau subjek di
> luar frame. **Ukur dulu pada 20 klip berlabel, baru klaim angkanya.**

Karena itu **presisi reframing ≥ 85% adalah target, bukan hasil terverifikasi**
(lihat `metrics.md`). Klaim tanpa pengukuran dilarang (Sprint 3 butir 31).

Sumber: PRD §6; TECH_SPEC §5.2, §6 Sprint 3 butir 27 & 31.

---

## 4. Ringkasan Risiko Utang yang Sudah Diakui

Satu risiko TECH_SPEC §7 yang terkait langsung dengan utang di atas **masih
berlaku**, dan dua risiko VPS **dibatalkan oleh T9**:

| Risiko | Mitigasi |
|---|---|
| Target OKR kecepatan tidak tercapai (akibat D1) | §0.1: metrik diganti + diukur di butir 19/42 |
| ~~CPU starvation di VPS~~ | **Dibatalkan T9** — tidak ada VPS; diganti batas paralel lokal (`RENDER_SLOTS`/`STT_SLOTS`, `constraints.md` §6) |
| ~~Backup tidak teruji~~ | **Dibatalkan T9** — tidak ada PostgreSQL/VPS yang di-backup |

Sumber: TECH_SPEC §7; T9.

---

## 5. BELUM DITENTUKAN

- **BELUM DITENTUKAN** — Kapan tepatnya GPU tersedia (semua tiga utang yang
  menunggu GPU memakai pemicu yang sama, tapi tidak ada tanggal/anggaran).
- **BELUM DITENTUKAN** — Pengganti konkret untuk diarization jangka panjang
  (mis. model apa saat GPU tersedia).
- **BELUM DITENTUKAN** — Rencana bila kelak butuh multi-pengguna/deployment:
  memakai di server berarti menulis ulang lapisan antrean/penyimpanan (T9),
  dan belum ada desainnya.
- **BELUM DITENTUKAN** — Tingkat keandalan heuristik energi+gap vs diarization
  sungguhan (belum ada pengukuran).

Sumber: TECH_SPEC §5.3, §7, §8.
