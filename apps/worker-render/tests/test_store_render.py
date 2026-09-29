"""Test penyimpanan hasil render: salinan manusia di ``output/clips``.

Salinan di ``output/clips`` harus hard link ke berkas kanonik (satu berkas
fisik), dan bila hard link gagal, salinan penuh tetap dibuat TETAPI tercatat
di log — tanpa jejak, klip ganda yang memakan ruang disk dua kali tidak bisa
dijelaskan belakangan.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest

WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Arahkan penyimpanan dan folder klip ke direktori sementara."""
    monkeypatch.setenv("LOCAL_STORAGE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("CLIPS_OUTPUT_DIR", str(tmp_path / "clips"))
    return tmp_path


def _rendered(tmp_path: Path) -> Path:
    source = tmp_path / "hasil.mp4"
    source.write_bytes(b"video-bytes" * 100)
    return source


def _human_copy(tmp_path: Path) -> Path:
    (copy,) = (tmp_path / "clips").glob("*.mp4")
    return copy


def test_salinan_klip_adalah_hard_link(storage: Path) -> None:
    from worker_render.tasks import _store_render

    _store_render("seg-1", "final", _rendered(storage), job_id="job-1", label="Judul")

    assert os.stat(_human_copy(storage)).st_nlink == 2


def test_hard_link_gagal_membuat_salinan_dan_mencatat_peringatan(
    storage: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from worker_render import tasks

    def _link_gagal(_src: object, _dst: object) -> None:
        raise OSError("beda volume")

    monkeypatch.setattr(tasks.os, "link", _link_gagal)
    expected = b"video-bytes" * 100

    with caplog.at_level(logging.WARNING, logger=tasks.__name__):
        tasks._store_render("seg-1", "final", _rendered(storage), job_id="job-1", label="Judul")

    copy = _human_copy(storage)
    assert copy.read_bytes() == expected
    assert os.stat(copy).st_nlink == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "beda volume" in warnings[0].getMessage()
