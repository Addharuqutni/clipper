"""Koneksi SQLite sinkron untuk worker.

API memakai SQLAlchemy async; worker memakai ``sqlite3`` langsung karena
berjalan di thread biasa dan hanya menjalankan query sederhana. Keduanya membuka
berkas yang sama: ``<LOCAL_STORAGE_DIR>/clipper.db``.

Query worker ditulis dengan placeholder ``%s``; proxy di bawah
menerjemahkannya ke ``?`` milik sqlite3.
"""

from __future__ import annotations

import contextlib
import os
import re
import sqlite3
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clipper_shared.storage import repo_root, storage_root

sqlite3.register_adapter(uuid.UUID, lambda u: str(u))


def to_db_timestamp(value: datetime) -> str:
    """Format waktu yang dipakai SEMUA penulis basis data.

    Sama dengan format ``DateTime`` SQLAlchemy di SQLite (UTC, spasi sebagai
    pemisah) sehingga ``ORDER BY created_at`` konsisten antara baris yang
    ditulis API dan baris yang ditulis worker. Format ISO dengan ``T`` dan
    ``+00:00`` akan selalu terurut di atas format spasi pada hari yang sama.
    """
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")


def utc_now() -> datetime:
    """Waktu sekarang (UTC, sadar zona)."""
    return datetime.now(UTC)


def get_sqlite_path(dsn: str | None = None) -> Path:
    """Jalur berkas SQLite dari ``DATABASE_URL`` atau lokasi bawaan."""
    url = (dsn or os.getenv("DATABASE_URL") or "").strip()
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            path = Path(url[len(prefix):])
            return path if path.is_absolute() else (repo_root() / path).resolve()
    return storage_root() / "clipper.db"


class SQLiteCursorProxy:
    """Kursor sqlite3 yang menerima placeholder ``%s``."""

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def description(self) -> Any:
        return self._cursor.description

    @staticmethod
    def _adapt_query(query: str) -> str:
        return re.sub(r"%s", "?", query.replace("now()", "CURRENT_TIMESTAMP"))

    @staticmethod
    def _adapt_params(params: Any) -> Any:
        if isinstance(params, (list, tuple)):
            return [to_db_timestamp(p) if isinstance(p, datetime) else p for p in params]
        return params

    def execute(self, query: str, params: Any = None) -> sqlite3.Cursor:
        sql = self._adapt_query(query)
        if params is None:
            return self._cursor.execute(sql)
        return self._cursor.execute(sql, self._adapt_params(params))

    def executemany(self, query: str, seq_of_parameters: Any) -> sqlite3.Cursor:
        return self._cursor.executemany(
            self._adapt_query(query), [self._adapt_params(p) for p in seq_of_parameters]
        )

    def fetchone(self) -> Any:
        return self._cursor.fetchone()

    def fetchall(self) -> list[Any]:
        return self._cursor.fetchall()

    def close(self) -> None:
        self._cursor.close()

    def __enter__(self) -> SQLiteCursorProxy:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class SQLiteConnectionProxy:
    """Koneksi sqlite3 dengan kursor :class:`SQLiteCursorProxy`."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def cursor(self) -> SQLiteCursorProxy:
        return SQLiteCursorProxy(self._conn.cursor())

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


@contextlib.contextmanager
def get_db_connection(dsn: str | None = None) -> Generator[SQLiteConnectionProxy, None, None]:
    """Buka koneksi, commit bila blok selesai normal, rollback bila exception.

    Usage::

        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
    """
    db_path = get_sqlite_path(dsn)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # timeout = busy_timeout: tunggu kunci tulis milik thread lain, jangan gagal.
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    proxy = SQLiteConnectionProxy(conn)
    try:
        yield proxy
        proxy.commit()
    except Exception:
        proxy.rollback()
        raise
    finally:
        proxy.close()
