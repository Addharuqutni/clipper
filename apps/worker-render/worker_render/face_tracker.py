"""Deteksi wajah MediaPipe Face Landmarker untuk pelacakan subjek.

Menggunakan **Face Landmarker** dari MediaPipe Tasks (bukan BlazeFace legacy),
karena landmark bibir diperlukan sebagai sinyal "sedang bicara" — ini cara
mendapatkan active speaker detection tanpa model khusus (TECH_SPEC §5.2).

**Dua jebakan yang ditangani modul ini:**

1. **Ruang warna.** OpenCV dan FFmpeg menghasilkan frame **BGR**; MediaPipe
   memerlukan **RGB**. Tanpa konversi, akurasi deteksi jatuh diam-diam: tidak
   ada error, hanya crop yang terus salah. Konversi dilakukan di dalam sini
   supaya pemanggil tidak bisa lupa.
2. **Timestamp video.** ``RunningMode.VIDEO`` mensyaratkan timestamp naik
   monoton. Timestamp yang sama atau mundur akan melempar error, jadi nilainya
   diturunkan dari nomor frame (bukan dari ``time.monotonic()``, yang bisa
   mundur saat penyesuaian jam sistem).

Modul ini hanya diimpor oleh worker-render. Container API dan worker-light
sengaja tidak memuat MediaPipe (TECH_SPEC §1: isolasi container).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Indeks landmark bibir pada Face Landmarker (478 titik).
#: Dipakai untuk menghitung bukaan mulut. Nilai ini berasal dari skema indeks
#: blazeface/facemesh yang sama dengan yang dipakai repo referensi.
LIP_INDICES: frozenset[int] = frozenset(
    {
        13, 14, 78, 81, 82, 84, 87, 88, 95, 146, 178, 181, 185, 191, 308, 312,
        317, 318, 324, 375, 402, 405, 409, 415,
    }
)

#: Ambang bukaan mulut untuk dianggap berbicara (TECH_SPEC §5.2).
MIN_SPEAK_RATIO = 0.18


@dataclass(frozen=True, slots=True)
class FaceObservation:
    """Satu wajah yang terdeteksi pada satu frame, dalam koordinat piksel."""

    center_x: float
    center_y: float
    width: float
    height: float
    #: True bila rasio bukaan mulut melewati ambang bicara.
    speaking: bool
    #: Rasio bukaan mulut mentah — disimpan untuk diagnostik/tuning.
    openness_ratio: float

    @property
    def area(self) -> float:
        return self.width * self.height


class FaceTracker:
    """Pembungkus Face Landmarker yang menjaga keadaan antar frame.

    Dipakai sebagai context manager supaya sumber daya native (model) pasti
    dibebaskan, termasuk saat terjadi pengecualian di tengah encoding.
    """

    def __init__(
        self,
        model_path: str | Path,
        *,
        num_faces: int = 10,
        min_detection_confidence: float = 0.35,
    ) -> None:
        self._model_path = str(model_path)
        self._num_faces = num_faces
        self._min_confidence = min_detection_confidence
        #: ``Any``, bukan ``object``: MediaPipe tidak membawa stub tipe resmi
        #: (lihat ``tool.mypy.overrides``), jadi ``create_from_options``
        #: mengembalikan ``Any``. Membatasi field ini ke ``object`` justru
        #: menghapus satu-satunya tipe yang kita tahu, sehingga setiap akses
        #: method (``detect_for_video``, ``close``) gagal di mypy.
        self._landmarker: Any | None = None
        self._last_timestamp_ms = -1
        #: Statistik untuk diagnostik — pengguna berhak tahu bila pelacakan
        #: praktis tidak bekerja (mis. wajah terlalu kecil pada rekaman jauh).
        self.frames_seen = 0
        self.frames_with_face = 0

    def __enter__(self) -> FaceTracker:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def open(self) -> None:
        """Muat model. Melempar bila MediaPipe atau modelnya tidak tersedia."""
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        if not Path(self._model_path).exists():
            raise FileNotFoundError(
                f"Model Face Landmarker tidak ditemukan di {self._model_path}. "
                "Jalankan start.cmd untuk mengunduhnya, atau "
                "setel FACE_LANDMARKER_MODEL."
            )

        options = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=self._model_path),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=self._num_faces,
            min_face_detection_confidence=self._min_confidence,
            # Landmark saja sudah cukup; blendshape menambah biaya CPU tanpa
            # dipakai — kita menghitung bukaan mulut sendiri dari landmark.
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)
        logger.info(
            "Face Landmarker siap (num_faces=%d, min_conf=%.2f)",
            self._num_faces,
            self._min_confidence,
        )

    def close(self) -> None:
        landmarker = self._landmarker
        self._landmarker = None
        if landmarker is not None:
            close = getattr(landmarker, "close", None)
            if callable(close):
                close()

    def detect(self, bgr_frame: bytes, *, frame_width: int, frame_height: int, frame_index: int, fps: float) -> list[FaceObservation]:
        """Deteksi wajah pada satu frame BGR mentah.

        Args:
            bgr_frame: Byte frame BGR (``frame_width * frame_height * 3``).
            frame_index: Nomor frame, dipakai untuk menghitung timestamp.
            fps: Laju frame video sumber.

        Returns:
            Daftar wajah yang terdeteksi, kosong bila tidak ada.
        """
        import numpy as np
        from mediapipe import Image as MpImage
        from mediapipe import ImageFormat

        landmarker = self._landmarker
        if landmarker is None:
            raise RuntimeError("FaceTracker belum dibuka; panggil open() lebih dulu.")

        # BGR -> RGB. Tanpa langkah ini, MediaPipe menerima saluran tertukar dan
        # akurasi deteksi turun tanpa error apa pun.
        array = np.frombuffer(bgr_frame, dtype=np.uint8).reshape((frame_height, frame_width, 3))
        rgb = array[:, :, ::-1]

        # Timestamp wajib naik monoton. Diturunkan dari nomor frame agar tidak
        # terpengaruh penyesuaian jam sistem (time.monotonic bisa mundur).
        timestamp_ms = int(frame_index / max(fps, 1e-6) * 1000.0)
        if timestamp_ms <= self._last_timestamp_ms:
            timestamp_ms = self._last_timestamp_ms + 1
        self._last_timestamp_ms = timestamp_ms

        image = MpImage(image_format=ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        result = landmarker.detect_for_video(image, timestamp_ms)

        self.frames_seen += 1
        landmarks_per_face = getattr(result, "face_landmarks", None) or []
        if not landmarks_per_face:
            return []

        self.frames_with_face += 1
        return [
            self._to_observation(landmarks, frame_width, frame_height)
            for landmarks in landmarks_per_face
        ]

    def _to_observation(self, landmarks: object, frame_width: int, frame_height: int) -> FaceObservation:
        """Ubah landmark ternormalisasi menjadi koordinat piksel + status bicara."""
        points = list(landmarks)  # type: ignore[call-overload]

        xs = [point.x * frame_width for point in points]
        ys = [point.y * frame_height for point in points]

        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)

        width = max(x_max - x_min, 1.0)
        height = max(y_max - y_min, 1.0)

        lip_ys = [
            point.y * frame_height
            for index, point in enumerate(points)
            if index in LIP_INDICES
        ]
        openness = (max(lip_ys) - min(lip_ys)) / height if len(lip_ys) >= 2 else 0.0

        return FaceObservation(
            center_x=(x_min + x_max) / 2.0,
            center_y=(y_min + y_max) / 2.0,
            width=width,
            height=height,
            speaking=openness > MIN_SPEAK_RATIO,
            openness_ratio=openness,
        )

    def tracking_health(self) -> str:
        """Ringkasan kualitas pelacakan, untuk log/diagnostik.

        Penting untuk dilaporkan: bila sebagian besar frame tidak punya wajah,
        crop akan diam di posisi terakhir dan pengguna akan mengira fitur rusak.
        """
        if self.frames_seen == 0:
            return "tidak ada frame diproses"
        ratio = self.frames_with_face / self.frames_seen
        if ratio == 0:
            return (
                f"TIDAK ADA wajah terdeteksi pada {self.frames_seen} frame — "
                "crop akan memakai posisi tengah"
            )
        if ratio < 0.3:
            return (
                f"wajah hanya terdeteksi pada {ratio * 100:.0f}% frame "
                f"({self.frames_with_face}/{self.frames_seen}) — pelacakan tidak stabil"
            )
        return f"wajah terdeteksi pada {ratio * 100:.0f}% frame"


def choose_subject(
    faces: list[FaceObservation],
    *,
    current_center_x: float,
    source_width: float,
    speaker_bonus: float = 2.0,
    continuity_weight: float = 0.4,
) -> FaceObservation | None:
    """Pilih wajah paling layak dijadikan subjek utama.

    Rumusnya dari repo referensi (TECH_SPEC §5.2): ukuran sebagai dasar, ditambah
    bonus bila sedang berbicara, dikurangi penalti bila jauh dari posisi crop
    sekarang. Penalti jarak memberi sifat "lengket" — kamera tidak berpindah
    hanya karena wajah lain melintas sekejap.
    """
    if not faces:
        return None
    if source_width <= 0:
        return faces[0]

    def score(face: FaceObservation) -> float:
        size_term = face.width / source_width
        continuity_term = abs(face.center_x - current_center_x) / source_width
        return size_term * (1.0 + speaker_bonus * (1.0 if face.speaking else 0.0)) - (
            continuity_weight * continuity_term
        )

    return max(faces, key=score)
