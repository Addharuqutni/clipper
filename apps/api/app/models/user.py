"""Model tabel ``users`` (TECH_SPEC §3: id, email, hashed_password, plan, created_at)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, created_at_column, uuid_pk

if TYPE_CHECKING:
    from app.models.job import Job


class User(Base):
    """Akun pengguna ClipperAI."""

    __tablename__ = "users"

    id: Mapped[UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    #: "free" | "pro" — belum ada enforcement kuota di MVP, hanya penanda.
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="free")
    #: Preset gaya subtitle yang dipakai bila job tidak punya override sendiri.
    #: ``NULL`` = pakai bawaan modul. ON DELETE SET NULL: menghapus preset tidak
    #: boleh menghapus pengguna.
    default_subtitle_preset_id: Mapped[UUID | None] = mapped_column(
        GUID(),
        ForeignKey("subtitle_presets.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = created_at_column()

    jobs: Mapped[list[Job]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="raise"
    )
