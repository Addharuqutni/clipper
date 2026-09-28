"""Base deklaratif dan tipe kolom bersama.

Semua model memakai ``Mapped[...]`` (SQLAlchemy 2.0 typed ORM). Nama kolom
mengikuti TECH_SPEC §3 **persis**, tanpa singkatan dan tanpa alias.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CHAR, JSON, DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

sqlite3.register_adapter(UUID, lambda u: str(u))


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:
    """Kompilasi JSONB Postgres menjadi tipe JSON standar di SQLite."""
    return "JSON"


class GUID(TypeDecorator[UUID]):
    """Platform-independent GUID/UUID type.

    Di PostgreSQL memakai PGUUID(as_uuid=True) bawaan.
    Di SQLite (atau DB lain) memakai CHAR(36), menyimpan string UUID berformat standar dengan strip (36 karakter),
    sehingga konsisten antara ORM dan query SQL mentah.
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PGUUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        if isinstance(value, UUID):
            return str(value)
        return str(UUID(str(value)))

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, UUID):
            return value
        return UUID(str(value))


class UTCDateTime(TypeDecorator[datetime]):
    """Waktu UTC: disimpan tanpa zona, dikembalikan SADAR zona (UTC).

    SQLite tidak menyimpan zona waktu. Tanpa tipe ini API mengembalikan waktu
    naif (``2026-09-27T14:41:10``) yang dibaca browser sebagai waktu lokal —
    meleset 7 jam di WIB.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value


#: Konvensi nama constraint supaya Alembic autogenerate stabil.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base declarative untuk seluruh model ClipperAI."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def uuid_pk() -> Mapped[UUID]:
    """Primary key UUID yang kompatibel dengan PostgreSQL dan SQLite."""
    return mapped_column(
        GUID(),
        primary_key=True,
        default=uuid4,
    )


def created_at_column() -> Mapped[datetime]:
    """``created_at`` UTC, default di sisi database."""
    return mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now(),
    )


def updated_at_column() -> Mapped[datetime]:
    """``updated_at`` yang diperbarui otomatis pada setiap UPDATE.

    Memakai ``onupdate`` (sisi aplikasi) **dan** ``server_default`` sehingga
    nilainya tetap benar baik ketika baris diubah lewat ORM maupun lewat SQL
    langsung. Berguna untuk pengaturan yang dapat diubah pengguna: kapan
    terakhir diubah sering menjadi pertanyaan saat menelusuri perilaku aneh.
    """
    return mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


def jsonb_column(default: Any = None) -> Mapped[dict[str, Any] | list[Any] | None]:
    """Kolom JSON/JSONB (JSONB di Postgres, JSON di SQLite)."""
    return mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
        default=default,
    )
