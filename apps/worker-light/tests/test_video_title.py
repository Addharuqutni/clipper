"""Judul video dari kedua sumber sampai ke basis data.

``jobs.video_title`` diisi dari dua arah: API memakai **nama berkas unggahan**
(``POST /uploads/init``), sedangkan worker baru tahu **judul YouTube** setelah
membaca metadata — lewat ``record_source_media``. Test ini menjaga sisi worker
pada SQLite sungguhan: judul YouTube benar-benar tersimpan, dan ingest ulang
job unggahan tidak menghapus judul yang sudah ada.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

#: Skema minimal: hanya kolom yang disentuh ``record_source_media``.
_SCHEMA = (
    "CREATE TABLE jobs (id TEXT PRIMARY KEY, video_title TEXT, updated_at TEXT)",
    """
    CREATE TABLE source_media (
        id TEXT PRIMARY KEY, job_id TEXT UNIQUE, r2_key TEXT,
        size_bytes INTEGER, duration_s REAL, width INTEGER, height INTEGER,
        codec TEXT, language TEXT, transcript_source TEXT, created_at TEXT
    )
    """,
)


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """SQLite berisi satu job, diarahkan ke ``tmp_path`` lewat env."""
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("LOCAL_STORAGE_DIR", str(tmp_path))
    path = tmp_path / "clipper.db"
    with closing(sqlite3.connect(path)) as connection:
        for statement in _SCHEMA:
            connection.execute(statement)
        connection.commit()
    return path


def _insert_job(path: Path, title: str | None) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("INSERT INTO jobs (id, video_title) VALUES ('job-1', ?)", (title,))
        connection.commit()


def _title(path: Path) -> str | None:
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute("SELECT video_title FROM jobs WHERE id = 'job-1'").fetchone()
    return row[0]


def _record(path: Path, *, video_title: str | None) -> None:
    """Panggil ``record_source_media`` seperti ``ingest_media`` melakukannya."""
    from worker_light import storage

    storage.record_source_media(
        job_id="job-1",
        r2_key="raw/job-1/video.mp4",
        size_bytes=1024,
        duration_s=60.0,
        width=1920,
        height=1080,
        codec="h264",
        language=None,
        transcript_source=None,
        video_title=video_title,
    )


def test_judul_youtube_dari_metadata_tersimpan(db: Path) -> None:
    _insert_job(db, None)

    _record(db, video_title="Judul Video YouTube")

    assert _title(db) == "Judul Video YouTube"


def test_ingest_ulang_tidak_menghapus_judul_unggahan(db: Path) -> None:
    """Jalur unggahan tidak mengirim judul; nama berkas dari API harus bertahan."""
    _insert_job(db, "rekaman-podcast.mp4")

    _record(db, video_title=None)

    assert _title(db) == "rekaman-podcast.mp4"
