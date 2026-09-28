"""Model tabel ``segments`` (TECH_SPEC §3).

Kolom: id, job_id, start_s, end_s, score, label, hook_score, completeness,
emotional_arc, reason, status[proposed|selected|rejected].

Indeks wajib: ``segments(job_id, score DESC)`` — Review Studio selalu menampilkan
kandidat terbaik lebih dulu.

Nilai ``status`` ditulis **persis** seperti di TECH_SPEC (``proposed``), bukan
``pending``: frontend dan kontrak LLM memakai istilah yang sama.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, created_at_column, uuid_pk

if TYPE_CHECKING:
    from app.models.job import Job
    from app.models.render import Render

#: Status kandidat segmen — TECH_SPEC §3 menuliskan ketiganya secara eksplisit.
SEGMENT_STATUSES: tuple[str, ...] = ("proposed", "selected", "rejected")


class Segment(Base):
    """Kandidat klip hasil scoring LLM, menunggu keputusan user."""

    __tablename__ = "segments"

    id: Mapped[UUID] = uuid_pk()
    job_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    start_s: Mapped[float] = mapped_column(Float, nullable=False)
    end_s: Mapped[float] = mapped_column(Float, nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hook_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    completeness: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: 0–1, sama seperti hook_score/completeness (dulu Text; nilai lama dikonversi SQLite).
    emotional_arc: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    created_at: Mapped[datetime] = created_at_column()

    job: Mapped[Job] = relationship(back_populates="segments", lazy="raise")
    renders: Mapped[list[Render]] = relationship(
        back_populates="segment", cascade="all, delete-orphan", lazy="raise"
    )

    __table_args__ = (
        CheckConstraint("end_s > start_s", name="time_range_valid"),
        # TECH_SPEC §3: status[proposed|selected|rejected]
        CheckConstraint("status IN ('proposed', 'selected', 'rejected')", name="status_valid"),
        # TECH_SPEC §3: indeks wajib segments(job_id, score DESC)
        Index("ix_segments_job_id_score_desc", "job_id", score.desc()),
    )
