"""Startup SQLite schema sync: kolom baru tidak boleh menghilangkan job lama.

``create_all`` tidak mengubah tabel yang sudah ada, jadi ``init_db_schema``
membangun ulang tabel yang tertinggal dari model (``_sync_sqlite_schema``).
Kolom baru yang NULLABLE harus muncul pada tabel hasil pembangunan ulang TANPA
menghapus baris yang sudah ada — kalau tidak, setiap penambahan kolom menjadi
operasi destruktif saat aplikasi di-restart.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from app.db.session import init_db_schema

#: Skema ``jobs`` SEBELUM kolom ``video_title`` ada.
_JOBS_TANPA_VIDEO_TITLE = """
CREATE TABLE jobs (
    id CHAR(32) NOT NULL PRIMARY KEY,
    user_id CHAR(32) NOT NULL,
    source_type VARCHAR(16) NOT NULL,
    source_url TEXT,
    status VARCHAR(32) NOT NULL,
    stage VARCHAR(32),
    progress INTEGER NOT NULL,
    clip_count INTEGER NOT NULL,
    language VARCHAR(8) NOT NULL DEFAULT 'id',
    subtitle_style JSON,
    error TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""


def test_startup_menambah_kolom_judul_tanpa_menghapus_job_lama(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("LOCAL_STORAGE_DIR", str(tmp_path))
    db_path = tmp_path / "clipper.db"
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute(_JOBS_TANPA_VIDEO_TITLE)
        connection.execute(
            "INSERT INTO jobs (id, user_id, source_type, source_url, status, stage, progress,"
            " clip_count, language, error, created_at, updated_at)"
            " VALUES ('job-lama', 'user-1', 'youtube', 'https://youtu.be/x', 'queued', 'ingest',"
            " 0, 5, 'id', NULL, '2026-09-01 10:00:00.000000', '2026-09-01 10:00:00.000000')"
        )
        connection.commit()

    asyncio.run(init_db_schema())

    with closing(sqlite3.connect(db_path)) as connection:
        columns = {row[1] for row in connection.execute('PRAGMA table_info("jobs")')}
        row = connection.execute("SELECT id, status, stage, video_title FROM jobs").fetchone()
    assert "video_title" in columns
    # Baris lama harus utuh; kolom baru terisi NULL, bukan menghapus job.
    assert row == ("job-lama", "queued", "ingest", None)
