"""Model tabel ``jobs`` (TECH_SPEC §3).

Kolom: id, user_id, source_type[upload|youtube], source_url, status, stage,
progress, error, created_at, updated_at.

Indeks wajib: ``jobs(user_id, created_at DESC)``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, UTCDateTime, created_at_column, uuid_pk

if TYPE_CHECKING:
    from app.models.job_event import JobEvent
    from app.models.segment import Segment
    from app.models.source_media import SourceMedia
    from app.models.transcript import Transcript
    from app.models.user import User


class Job(Base):
    """Satu pekerjaan pemrosesan video panjang → klip vertikal."""

    __tablename__ = "jobs"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: TECH_SPEC §3: source_type[upload|youtube]
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    #: NULL untuk source_type='upload'.
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: queued|running|done|failed|canceled — lifecycle tingkat job.
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    #: Tahap detail: ingest|transcribe|analyze|render|done.
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: 0–100, dipakai oleh SSE progress bar.
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Jumlah klip yang diminta pengguna (1–30). Diteruskan ke tahap scoring
    #: sebagai ``target_count``; disimpan di sini agar tidak perlu dioper
    #: manual lewat setiap ``send_task`` di rantai ingest → transcribe → score.
    clip_count: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    #: Override gaya subtitle untuk job INI (JSONB). ``NULL`` berarti memakai
    #: preset bawaan pengguna, atau bawaan modul bila preset juga tidak ada.
    #: Bentuknya bebas (ditentukan ``SubtitleStyle``) karena ia berkembang
    #: seiring bertambahnya opsi visual; kolom terpisah per properti akan
    #: memaksa migrasi untuk setiap tambahan opsi.
    subtitle_style: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped[User] = relationship(back_populates="jobs", lazy="raise")
    source_media: Mapped[SourceMedia | None] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan", lazy="raise"
    )
    transcripts: Mapped[list[Transcript]] = relationship(
        back_populates="job", cascade="all, delete-orphan", lazy="raise"
    )
    segments: Mapped[list[Segment]] = relationship(
        back_populates="job", cascade="all, delete-orphan", lazy="raise"
    )
    events: Mapped[list[JobEvent]] = relationship(
        back_populates="job", cascade="all, delete-orphan", lazy="raise"
    )

    __table_args__ = (
        CheckConstraint("source_type IN ('upload', 'youtube')", name="source_type_valid"),
        CheckConstraint("progress BETWEEN 0 AND 100", name="progress_range"),
        # Rentang yang sama dengan validator LLM (scoring.py MIN/MAX_SEGMENTS).
        CheckConstraint("clip_count BETWEEN 1 AND 30", name="clip_count_range"),
        CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed', 'canceled')",
            name="status_valid",
        ),
        # TECH_SPEC §3: indeks wajib jobs(user_id, created_at DESC) — daftar job
        # per user selalu diurutkan terbaru dulu.
        Index("ix_jobs_user_id_created_at_desc", "user_id", created_at.desc()),
        Index("ix_jobs_status", "status"),
    )
