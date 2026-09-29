"""Frame analisis pelacakan wajah lebih kecil dari sumber; posisi tetap di ruang sumber.

Pelacakan membaca frame yang diperkecil (lebar <= ``ANALYSIS_MAX_WIDTH``) demi
kecepatan. Bila ukuran sumber tidak diteruskan ke :meth:`FaceTracker.detect`,
semua posisi wajah jatuh di ruang 640 px dan crop diam-diam bergeser ke kiri
pada video 1080p — tanpa error apa pun.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ((1920, 1080), (640, 360)),
        ((3840, 2160), (640, 360)),
        ((1280, 720), (640, 360)),
        # Tinggi hasil pembulatan harus genap (syarat scale FFmpeg ke rgb24).
        ((1000, 563), (640, 360)),
        # Sumber sudah kecil: tidak diperbesar.
        ((640, 360), (640, 360)),
        ((480, 854), (480, 854)),
    ],
)
def test_ukuran_frame_analisis(source: tuple[int, int], expected: tuple[int, int]) -> None:
    from worker_render.reframer import analysis_size

    width, height = analysis_size(*source)
    assert (width, height) == expected
    assert height % 2 == 0


def test_posisi_wajah_dari_frame_kecil_dipetakan_ke_ukuran_sumber(monkeypatch: pytest.MonkeyPatch) -> None:
    np = pytest.importorskip("numpy")
    from worker_render.face_tracker import FaceTracker

    # MediaPipe tidak dibutuhkan untuk kontrak ini: Image cukup menyimpan data.
    fake_mp = types.ModuleType("mediapipe")
    fake_mp.Image = lambda image_format, data: data  # type: ignore[attr-defined]
    fake_mp.ImageFormat = types.SimpleNamespace(SRGB="srgb")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mediapipe", fake_mp)

    point = types.SimpleNamespace
    # Wajah di tengah horizontal (x 0,45..0,55), landmark ternormalisasi.
    face = [point(x=0.45, y=0.3), point(x=0.55, y=0.5)]
    seen: dict[str, Any] = {}

    class _Landmarker:
        def detect_for_video(self, image: Any, timestamp_ms: int) -> Any:
            seen["shape"] = image.shape
            return types.SimpleNamespace(face_landmarks=[face])

    tracker = FaceTracker("unused.task")
    tracker._landmarker = _Landmarker()

    frame = np.zeros((360, 640, 3), dtype=np.uint8).tobytes()
    (observation,) = tracker.detect(
        frame,
        frame_width=640,
        frame_height=360,
        frame_index=0,
        fps=30.0,
        source_width=1920,
        source_height=1080,
    )

    assert seen["shape"] == (360, 640, 3)
    assert observation.center_x == pytest.approx(960.0)
    assert observation.center_y == pytest.approx(432.0)
    assert observation.width == pytest.approx(192.0)
