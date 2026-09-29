"""Bahasa job yang dipilih pengguna sampai ke pemilih subtitle dan Whisper.

Pilihan eksplisit (``id``/``en``) wajib dihormati: video berbahasa Indonesia
yang hanya punya subtitle Inggris harus ditranskripsi Whisper, bukan memakai
terjemahan. ``auto`` diteruskan sebagai ``None`` (deteksi otomatis).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))

from clipper_shared.stt import TranscriptResult, TranscriptWord  # noqa: E402
from clipper_shared.subtitles import SubtitleTrack  # noqa: E402

from worker_light import media_fetcher, storage, tasks  # noqa: E402
from worker_light.media_fetcher import YoutubeMetadata  # noqa: E402


@pytest.fixture
def pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Semua I/O ingest/transkripsi diganti tiruan; yang dicatat: pilihan dan urutan."""
    record: dict[str, Any] = {"emits": [], "submitted": [], "subtitle_downloads": 0}
    settings = {"language": "id"}

    monkeypatch.setattr(tasks, "emit", lambda job_id, status, stage, progress, message=None: record["emits"].append((stage, progress, message)))
    monkeypatch.setattr(tasks, "submit", lambda name, *args: record["submitted"].append(name))
    monkeypatch.setattr(tasks, "make_workspace", lambda prefix: tmp_path)
    monkeypatch.setattr(tasks, "max_video_minutes", lambda _tokens: 180)
    monkeypatch.setattr(
        tasks,
        "_load_job_settings",
        lambda job_id: (5, None if settings["language"] == "auto" else settings["language"]),
    )

    monkeypatch.setattr(storage, "load_provider_config", lambda job_id: {})
    monkeypatch.setattr(storage, "fetch_youtube_cookies", lambda job_id, work_dir: None)
    monkeypatch.setattr(storage, "probe_media", lambda path, job_id: {"size_bytes": 1, "duration_s": 60.0, "width": 1920, "height": 1080, "codec": "h264"})
    monkeypatch.setattr(storage, "store_downloaded_media", lambda job_id, path: "raw/x.mp4")
    monkeypatch.setattr(storage, "record_source_media", lambda **kwargs: record.setdefault("source_media", kwargs))
    monkeypatch.setattr(storage, "save_transcript", lambda **kwargs: record.setdefault("transcript", kwargs))
    monkeypatch.setattr(storage, "save_segments", lambda **kwargs: None)
    monkeypatch.setattr(storage, "source_media_path", lambda job_id: tmp_path / "src.mp4")
    monkeypatch.setattr(storage, "extract_audio", lambda media, work, job_id: tmp_path / "a.wav")

    english_only = YoutubeMetadata(
        video_id="abc",
        title="t",
        duration_s=60.0,
        is_live=False,
        is_private=False,
        uploader="u",
        language="id",
        manual_tracks=[SubtitleTrack(lang="en", ext="json3", url="https://s")],
        automatic_tracks=[],
    )
    monkeypatch.setattr(media_fetcher, "fetch_youtube_metadata", lambda url, cookies, job_id: english_only)
    monkeypatch.setattr(media_fetcher, "validate_duration", lambda metadata, max_minutes: None)
    monkeypatch.setattr(media_fetcher, "download_youtube", lambda url, work, cookies, job_id: tmp_path / "v.mp4")

    def fake_download_subtitle(track: SubtitleTrack, work: Path, cookies: Path | None) -> tuple[list[Any], str]:
        record["subtitle_downloads"] += 1
        return [type("W", (), {"text": "hello", "start_s": 0.0, "end_s": 1.0})()], "json3"

    monkeypatch.setattr(media_fetcher, "download_subtitle", fake_download_subtitle)

    class FakeTranscriber:
        def transcribe(self, audio_path: str, language: str | None, on_progress: Any = None) -> TranscriptResult:
            record["whisper_language"] = language
            return TranscriptResult(
                language=language or "id",
                full_text="halo",
                words=[TranscriptWord(start_s=0.0, end_s=1.0, word="halo")],
                model_used="fake",
            )

    monkeypatch.setattr(tasks, "_transcriber", lambda job_id: FakeTranscriber())
    record["settings"] = settings
    return record


def test_job_id_dengan_subtitle_inggris_saja_memakai_whisper(pipeline: dict[str, Any]) -> None:
    tasks.ingest_media("job-1", "youtube", "https://www.youtube.com/watch?v=abc")

    assert pipeline["subtitle_downloads"] == 0
    assert pipeline["submitted"] == ["worker_light.tasks.transcribe_media"]


def test_job_en_memakai_subtitle_inggris(pipeline: dict[str, Any]) -> None:
    pipeline["settings"]["language"] = "en"
    tasks.ingest_media("job-1", "youtube", "https://www.youtube.com/watch?v=abc")

    assert pipeline["subtitle_downloads"] == 1
    assert pipeline["submitted"] == ["worker_light.tasks.score_segments"]


@pytest.mark.parametrize(("stored", "expected"), [("id", "id"), ("en", "en"), ("auto", None)])
def test_transkripsi_memakai_bahasa_job(pipeline: dict[str, Any], stored: str, expected: str | None) -> None:
    pipeline["settings"]["language"] = stored
    tasks.transcribe_media("job-1")

    assert pipeline["whisper_language"] == expected
    assert pipeline["submitted"] == ["worker_light.tasks.score_segments"]


def test_progres_upload_sampai_analisis_tidak_pernah_turun(pipeline: dict[str, Any]) -> None:
    tasks.ingest_media("job-1", "upload")
    tasks.transcribe_media("job-1")

    progress = [value for _stage, value, _message in pipeline["emits"]]
    assert progress == sorted(progress), progress
    assert pipeline["emits"][-1][:2] == ("analyze", tasks.TRANSCRIBE_END)
