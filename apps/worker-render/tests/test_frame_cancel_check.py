"""Pemeriksaan pembatalan saat membaca frame FFmpeg dibatasi per waktu.

Pelacakan wajah membaca ribuan frame; memeriksa basis data untuk setiap frame
berarti satu koneksi SQLite per frame. Pembatalan tetap harus terlihat dalam
hitungan ``POLL_INTERVAL_S``, bukan setelah seluruh segmen selesai dibaca.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any

import pytest

WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))

WIDTH, HEIGHT, FRAMES = 4, 2, 200
FRAME_BYTES = WIDTH * HEIGHT * 3


class _FakeProcess:
    """Proses FFmpeg tiruan yang menulis ``FRAMES`` frame mentah ke stdout."""

    def __init__(self) -> None:
        self.stdout = io.BytesIO(b"\x00" * FRAME_BYTES * FRAMES)

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0


class _Clock:
    """Jam monotonik tiruan: maju ``step`` detik setiap dibaca."""

    def __init__(self, step: float) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


@pytest.fixture
def pipe(monkeypatch: pytest.MonkeyPatch) -> Any:
    from worker_render import ffmpeg_pipe

    monkeypatch.setattr(ffmpeg_pipe, "spawn", lambda *_a, **_k: _FakeProcess())
    monkeypatch.setattr(ffmpeg_pipe, "unregister", lambda *_a: None)
    return ffmpeg_pipe


def test_pembacaan_frame_tidak_memeriksa_db_per_frame(pipe: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    checks: list[str] = []
    monkeypatch.setattr(pipe, "is_canceled", lambda job_id: checks.append(job_id) or False)
    # 200 frame dibaca dalam ±1 detik jam tiruan → cukup beberapa pemeriksaan.
    monkeypatch.setattr(pipe.time, "monotonic", _Clock(step=0.005))

    frames = list(pipe.iter_video_frames(Path("x.mp4"), width=WIDTH, height=HEIGHT, job_id="job-1"))

    assert len(frames) == FRAMES
    assert 1 <= len(checks) <= 5, f"{len(checks)} pemeriksaan untuk {FRAMES} frame"


def test_pembatalan_tetap_terlihat_di_tengah_pembacaan(pipe: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from clipper_shared.worker_events import JobCanceled

    monkeypatch.setattr(pipe, "is_canceled", lambda _job_id: True)
    monkeypatch.setattr(pipe.time, "monotonic", _Clock(step=0.1))

    read = 0
    with pytest.raises(JobCanceled):
        for _frame in pipe.iter_video_frames(Path("x.mp4"), width=WIDTH, height=HEIGHT, job_id="job-1"):
            read += 1

    assert read < FRAMES
