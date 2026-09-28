"""Penghalusan posisi crop untuk mode pelacakan wajah.

Modul ini **bebas dari MediaPipe/OpenCV** dengan sengaja: ia hanya memuat
mesin keadaan (state machine) penghalusan. Deteksi wajah dilakukan di
worker-render, lalu posisi wajah mentah dikirim ke sini. Pemisahan ini membuat
logika tersulit — yang menentukan crop terlihat halus atau goyang — bisa diuji
tanpa video dan tanpa model ML.

Nilai tuning diambil dari repo referensi `jipraks/yt-short-clipper`
(``portrait.py``); lihat docs/memory/references.md dan TECH_SPEC §5.2.

Tiga perilaku yang membuat hasilnya terasa "dikendalikan manusia":

1. **EMA** — kamera bergerak menuju target, tidak melompat. ``EMA_ALPHA`` 0.15
   berarti sekitar 15% jarak ditempuh per frame.
2. **Deadband** — pergerakan lebih kecil dari 2% lebar crop diabaikan. Tanpa
   ini, getaran mikro wajah membuat gambar bergetar terus.
3. **Snap** — lompatan lebih besar dari 35% lebar crop dianggap ganti adegan
   atau ganti pembicara, dan kamera langsung pindah tanpa penghalusan. Tanpa
   ini, kamera akan "meluncur" lambat melintasi seluruh frame, yang terasa
   jauh lebih buruk daripada potongan langsung.

Bila wajah tidak terdeteksi, posisi terakhir **dipertahankan** — bukan kembali
ke tengah. Wajah yang menghilang sesaat (menoleh, tertutup tangan) lebih baik
daripada kamera yang tiba-tiba melompat ke tengah.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Kecepatan kamera mengikuti target. Lebih kecil = lebih halus tapi lebih lambat.
EMA_ALPHA = 0.15

#: Abaikan gerakan lebih kecil dari fraksi ini terhadap lebar crop (anti-jitter).
DEADZONE_FRAC = 0.02

#: Lompatan lebih besar dari fraksi ini dianggap ganti adegan -> pindah langsung.
SNAP_FRAC = 0.35


@dataclass(slots=True)
class CropSmoother:
    """Penghalus posisi tengah crop.

    Dipakai per frame secara berurutan; menyimpan posisi yang sudah dihaluskan
    dan posisi wajah terakhir yang diketahui.
    """

    crop_width: float
    source_width: float
    initial_center: float | None = None
    alpha: float = EMA_ALPHA
    deadzone_frac: float = DEADZONE_FRAC
    snap_frac: float = SNAP_FRAC
    #: Jumlah frame yang wajahnya tidak terdeteksi. Dipakai untuk diagnostik.
    missed_frames: int = 0
    _smoothed: float = field(init=False, repr=False)
    _last_face: float | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.crop_width <= 0:
            raise ValueError("crop_width harus positif")
        start = self.initial_center if self.initial_center is not None else self.source_width / 2.0
        self._smoothed = start
        self._last_face = None

    @property
    def smoothed_center(self) -> float:
        """Posisi tengah crop saat ini, sudah dihaluskan."""
        return self._smoothed

    @property
    def is_tracking(self) -> bool:
        """True bila wajah pernah terdeteksi pada frame mana pun."""
        return self._last_face is not None

    def update(self, face_center: float | None) -> float:
        """Perbarui dengan posisi wajah baru, kembalikan posisi crop yang dipakai.

        Args:
            face_center: Titik tengah horizontal wajah pada frame ini, atau
                ``None`` bila tidak ada wajah terdeteksi.

        Returns:
            Titik tengah crop yang sudah dihaluskan (dalam koordinat sumber).
        """
        if face_center is None:
            # Pertahankan posisi terakhir. Kalau belum pernah ada wajah,
            # tetap di tengah.
            self.missed_frames += 1
            return self._smoothed

        self._last_face = face_center
        delta = face_center - self._smoothed
        deadzone = self.deadzone_frac * self.crop_width
        snap_distance = self.snap_frac * self.crop_width

        if abs(delta) > snap_distance:
            # Ganti adegan / ganti pembicara: pindah langsung, tanpa luncur.
            self._smoothed = face_center
        elif abs(delta) > deadzone:
            self._smoothed += self.alpha * delta
        # Sisa kasus: pergerakan terlalu kecil, biarkan posisi apa adanya.

        return self._smoothed

    def crop_x(self) -> int:
        """Posisi x kiri crop dalam piksel, dijaga agar tidak keluar bingkai.

        Crop tidak boleh melewati tepi video: bagian di luar bingkai akan
        menjadi hitam atau membuat FFmpeg error.
        """
        half = self.crop_width / 2.0
        left = self._smoothed - half
        maximum = self.source_width - self.crop_width
        if maximum < 0:
            # Sumber lebih sempit dari lebar crop: hanya mungkin di tengah.
            return 0
        return int(max(0.0, min(left, maximum)))


def score_face_candidate(
    face_width: float,
    face_center_x: float,
    current_center_x: float,
    source_width: float,
    speaking: bool,
    speaker_bonus: float = 2.0,
    continuity_weight: float = 0.4,
) -> float:
    """Nilai kelayakan satu wajah untuk dijadikan subjek utama.

    Rumusnya dari repo referensi: ukuran sebagai dasar, ditambah bonus bila
    sedang berbicara, dikurangi penalti bila jauh dari posisi crop sekarang.

    ``speaker_bonus`` membuat pembicara aktif lebih mungkin dipilih walau
    wajahnya lebih kecil (mis. pembicara duduk agak jauh). ``continuity_weight``
    memberi sifat "lengket": kamera tidak berpindah hanya karena wajah lain
    melintas sekejap.

    Args:
        speaking: True bila rasio bukaan mulut melewati ambang bicara.
    """
    if source_width <= 0:
        return float("-inf")

    size_term = face_width / source_width
    continuity_term = abs(face_center_x - current_center_x) / source_width

    return size_term * (1.0 + speaker_bonus * (1.0 if speaking else 0.0)) - (
        continuity_weight * continuity_term
    )


def mouth_is_speaking(
    lip_ys: list[float],
    face_height: float,
    min_ratio: float = 0.18,
) -> bool:
    """Tentukan apakah mulut sedang terbuka cukup lebar untuk disebut bicara.

    ``lip_ys`` adalah koordinat Y titik-titik bibir (sudah dalam piksel);
    ``face_height`` tinggi wajah dalam piksel.

    Ambang 0.18 berasal dari repo referensi. Nilai ini sengaja tidak terlalu
    rendah: bibir yang sedikit terbuka saat diam akan membuat kamera berpindah
    ke orang yang salah.
    """
    if len(lip_ys) < 2 or face_height <= 0:
        return False
    openness = (max(lip_ys) - min(lip_ys)) / face_height
    return openness > min_ratio
