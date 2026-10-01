"""Test penyimpanan hasil render per job.

Salinan di ``output/clips/<job_id>`` harus hard link ke berkas kanonik (satu
berkas fisik), dan bila hard link gagal, salinan penuh tetap dibuat TETAPI
tercatat di log.
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


def _rendered(tmp_path: Path, name: str = "hasil.mp4") -> Path:
    source = tmp_path / name
    source.write_bytes(b"video-bytes" * 100)
    return source


def _human_copy(tmp_path: Path, job_id: str = "job-1") -> Path:
    (copy,) = (tmp_path / "clips" / job_id).glob("*.mp4")
    return copy


def test_salinan_klip_adalah_hard_link(storage: Path) -> None:
    from worker_render.tasks import _store_render

    job_id = "4d6d1fb7-9a1a-4b61-bc25-3f272e971293"
    _store_render("seg-1", "final", _rendered(storage), job_id=job_id, label="Judul")

    assert os.stat(_human_copy(storage, job_id)).st_nlink == 2


def test_setiap_job_mendapat_folder_output_sendiri(storage: Path) -> None:
    from worker_render.tasks import _store_render

    job_one = "4d6d1fb7-9a1a-4b61-bc25-3f272e971293"
    job_two = "8c1a2e4f-6b7d-4e90-a123-456789abcdef"
    _store_render("seg-1", "final", _rendered(storage, "hasil-1.mp4"), job_id=job_one, label="Judul")
    _store_render("seg-2", "final", _rendered(storage, "hasil-2.mp4"), job_id=job_two, label="Judul")

    assert _human_copy(storage, job_one).parent == storage / "clips" / job_one
    assert _human_copy(storage, job_two).parent == storage / "clips" / job_two


def test_id_job_tidak_boleh_keluar_dari_root_output(storage: Path) -> None:
    from worker_render.tasks import _job_clips_dir

    with pytest.raises(ValueError, match="ID job tidak valid"):
        _job_clips_dir("../job-lain")


def test_hard_link_gagal_membuat_salinan_dan_mencatat_peringatan(
    storage: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from worker_render import tasks

    def _link_gagal(_src: object, _dst: object) -> None:
        raise OSError("beda volume")

    monkeypatch.setattr(tasks.os, "link", _link_gagal)
    expected = b"video-bytes" * 100
    job_id = "4d6d1fb7-9a1a-4b61-bc25-3f272e971293"

    with caplog.at_level(logging.WARNING, logger=tasks.__name__):
        tasks._store_render("seg-1", "final", _rendered(storage), job_id=job_id, label="Judul")

    copy = _human_copy(storage, job_id)
    assert copy.read_bytes() == expected
    assert os.stat(copy).st_nlink == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "beda volume" in warnings[0].getMessage()
