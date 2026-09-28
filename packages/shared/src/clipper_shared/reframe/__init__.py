"""Paket reframing video.

Dua tanggung jawab yang dipisah:

* :mod:`clipper_shared.reframe.modes` — pilihan mode (pelacakan wajah / bar
  hitam / blur latar) dan perakitan rantai filter FFmpeg.
* :mod:`clipper_shared.reframe.tracker` — penghalusan posisi crop (EMA +
  deadband + snap) dan pemilihan subjek.

Keduanya **bebas dari OpenCV/MediaPipe** agar bisa diuji di container yang
tidak memuat dependensi berat (TECH_SPEC §1: isolasi container A/B/C).
Deteksi wajah dilakukan di ``worker-render`` dan hanya mengirim koordinat ke
sini.
"""

from __future__ import annotations

from clipper_shared.reframe.modes import (
    BLUR_SIGMA,
    DEFAULT_CROP_MODE,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    CropGeometry,
    CropMode,
    build_filter_chain,
    crop_width_for,
    describe_mode,
    plan_letterbox,
)
from clipper_shared.reframe.tracker import (
    DEADZONE_FRAC,
    EMA_ALPHA,
    SNAP_FRAC,
    CropSmoother,
    mouth_is_speaking,
    score_face_candidate,
)

__all__ = [
    "BLUR_SIGMA",
    "DEADZONE_FRAC",
    "DEFAULT_CROP_MODE",
    "EMA_ALPHA",
    "OUTPUT_HEIGHT",
    "OUTPUT_WIDTH",
    "SNAP_FRAC",
    "CropGeometry",
    "CropMode",
    "CropSmoother",
    "build_filter_chain",
    "crop_width_for",
    "describe_mode",
    "mouth_is_speaking",
    "plan_letterbox",
    "score_face_candidate",
]
