"""Test unit mendalam untuk clipper_shared.job_media."""

from __future__ import annotations

from pathlib import Path

import pytest
from clipper_shared import job_media
from clipper_shared import storage as layout


def test_job_source_key_generation() -> None:
    key = job_media.job_source_key("job-123", "Video Podcast Eps 1", "sample.mp4")
    assert key.startswith("video-podcast-eps-1-")
    assert key.endswith("/sample.mp4")


def test_job_source_key_fallback_to_filename() -> None:
    key = job_media.job_source_key("job-456", None, "my_recording.mov")
    assert key.startswith("my-recording-")
    assert key.endswith("/my_recording.mov")


def test_job_dir_raises_when_no_media(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job_media, "source_key", lambda job_id: None)
    with pytest.raises(job_media.MediaNotFoundError) as exc:
        job_media.job_dir("job-missing")
    assert "belum punya media sumber" in str(exc.value)


def test_source_path_raises_when_no_media(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job_media, "source_key", lambda job_id: None)
    with pytest.raises(job_media.MediaNotFoundError) as exc:
        job_media.source_path("job-missing")
    assert "tidak ditemukan" in str(exc.value)


def test_source_path_raises_when_file_deleted_from_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(layout, "storage_root", lambda: tmp_path)
    monkeypatch.setattr(job_media, "source_key", lambda job_id: "slug-job-1/missing.mp4")

    with pytest.raises(job_media.MediaNotFoundError) as exc:
        job_media.source_path("job-1")
    assert "tidak ada di disk" in str(exc.value)


def test_store_and_purge_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(layout, "storage_root", lambda: tmp_path)
    monkeypatch.setattr(job_media, "source_key", lambda job_id: None)

    # Buat file sementara untuk disimpan
    incoming = tmp_path / "incoming.mp4"
    incoming.write_bytes(b"dummy video data")

    key = job_media.store("job-test", "video.mp4", incoming, title="Tes Video")
    assert key.startswith("tes-video-")
    assert key.endswith("/video.mp4")
    assert not incoming.exists()  # dipindahkan (moved)

    # Simulasikan source_key terdaftar di DB
    monkeypatch.setattr(job_media, "source_key", lambda job_id: key)

    resolved_path = job_media.source_path("job-test")
    assert resolved_path.is_file()
    assert resolved_path.read_bytes() == b"dummy video data"

    # Simpan klip kedua: harus menggunakan folder yang sama
    second_clip = tmp_path / "clip.mp4"
    second_clip.write_bytes(b"clip content")
    key_clip = job_media.store("job-test", "clip.mp4", second_clip)
    assert key_clip.startswith(key.split("/")[0])
    assert key_clip.endswith("/clip.mp4")

    # Purge
    assert job_media.purge("job-test") is True
    assert not resolved_path.exists()
    assert not job_media.job_dir("job-test").exists()
