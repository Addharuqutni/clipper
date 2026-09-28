"""Sesi SQLAlchemy async ke SQLite.

Satu ``AsyncSession`` per request (FastAPI dependency). Worker membuka berkas
yang sama lewat ``sqlite3`` (``clipper_shared.db``).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

logger = logging.getLogger(__name__)


def _create_engine() -> AsyncEngine:
    engine = create_async_engine(settings.database_url, echo=settings.DB_ECHO)

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_conn: Any, _record: Any) -> None:
        # Sama dengan koneksi worker: tunggu kunci tulis thread lain (bukan
        # langsung "database is locked"), WAL untuk baca-tulis bersamaan, dan
        # foreign key ditegakkan (SQLite mematikannya secara bawaan).
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA busy_timeout = 30000")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    return engine


engine: AsyncEngine = _create_engine()

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db_schema() -> None:
    """Buat tabel yang belum ada dan selaraskan tabel yang skemanya tertinggal.

    Memakai engine SINKRON terpisah tanpa ``PRAGMA foreign_keys``: pembangunan
    ulang tabel menghapus tabel lama, dan dengan foreign key aktif penghapusan
    itu ikut menghapus baris anak (ON DELETE CASCADE).
    """
    from sqlalchemy import create_engine

    import app.models  # noqa: F401 — mendaftarkan semua model ke Base.metadata
    from app.models.base import Base

    sync_engine = create_engine(settings.database_url.replace("+aiosqlite", ""))
    try:
        with sync_engine.begin() as conn:
            Base.metadata.create_all(conn)
            _sync_sqlite_schema(conn, Base.metadata)
            _normalize_timestamps(conn, Base.metadata)
    finally:
        sync_engine.dispose()
    logger.info("Skema basis data SQLite siap.")


def _sync_sqlite_schema(conn: Any, metadata: Any) -> None:
    """Bangun ulang tabel SQLite yang skemanya tertinggal dari model.

    ``create_all`` tidak mengubah tabel yang sudah ada. Tanpa ini, kolom baru
    hilang (``no such column``) dan CHECK lama tetap berlaku. Tabel dianggap
    tertinggal bila DDL di ``sqlite_master`` berbeda dari DDL model; prosedur
    pembangunan ulang mengikuti https://www.sqlite.org/lang_altertable.html#otheralter.
    ``legacy_alter_table`` menjaga foreign key tabel lain tetap menunjuk nama asli.
    """
    from sqlalchemy import text
    from sqlalchemy.schema import CreateIndex, CreateTable

    stored = dict(conn.execute(text("SELECT name, sql FROM sqlite_master WHERE type = 'table'")).all())
    drifted = [
        table
        for table in metadata.tables.values()
        if stored.get(table.name) not in (None, str(CreateTable(table).compile(dialect=conn.dialect)).strip())
    ]
    if not drifted:
        return

    conn.execute(text("PRAGMA legacy_alter_table = ON"))
    try:
        for table in drifted:
            old = f"_old_{table.name}"
            existing = {row[1] for row in conn.execute(text(f'PRAGMA table_info("{table.name}")'))}
            columns = ", ".join(f'"{c.name}"' for c in table.columns if c.name in existing)
            for index in table.indexes:
                conn.execute(text(f'DROP INDEX IF EXISTS "{index.name}"'))
            conn.execute(text(f'ALTER TABLE "{table.name}" RENAME TO "{old}"'))
            conn.execute(CreateTable(table))
            # Nama tabel/kolom berasal dari metadata model, bukan masukan pengguna.
            conn.execute(text(f'INSERT INTO "{table.name}" ({columns}) SELECT {columns} FROM "{old}"'))  # noqa: S608
            conn.execute(text(f'DROP TABLE "{old}"'))
            for index in table.indexes:
                conn.execute(CreateIndex(index))
            logger.info("Skema SQLite diperbarui: tabel %s dibangun ulang.", table.name)
    finally:
        conn.execute(text("PRAGMA legacy_alter_table = OFF"))


def _normalize_timestamps(conn: Any, metadata: Any) -> None:
    """Ubah timestamp ISO lama (``2026-09-27T14:41:10.1+00:00``) ke format bersama.

    Versi lama worker menulis ISO dengan ``T`` dan offset, API menulis dengan
    spasi. Pada hari yang sama semua baris ``T`` terurut di atas baris spasi,
    sehingga "render terbaru" salah dipilih. Satu kali per baris lama; baris
    baru sudah memakai format yang sama (``clipper_shared.db.to_db_timestamp``).
    """
    from sqlalchemy import DateTime, text

    for table in metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, DateTime) or isinstance(getattr(column.type, "impl", None), DateTime):
                # Nama tabel/kolom berasal dari metadata model, bukan masukan pengguna.
                sql = (
                    f'UPDATE "{table.name}" SET "{column.name}" = '  # noqa: S608
                    f"replace(replace(\"{column.name}\", 'T', ' '), '+00:00', '') "
                    f"WHERE \"{column.name}\" LIKE '%T%'"
                )
                conn.execute(text(sql))


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency FastAPI: satu sesi per request, selalu ditutup."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def check_database() -> bool:
    """Healthcheck ringan untuk ``/health/ready`` (SELECT 1)."""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


async def dispose_engine() -> None:
    """Tutup pool saat shutdown aplikasi."""
    await engine.dispose()
