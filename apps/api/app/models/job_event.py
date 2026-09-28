"""Model tabel ``job_events`` (TECH_SPEC §3).

Kolom: id, job_id, stage, message, payload JSONB, created_at

Indeks wajib: ``job_events(job_id, created_at)``.

Tabel ini adalah log append-only yang menjadi sumber SSE: setiap perubahan
tahap ditulis ke DB (untuk replay/audit) DAN dipublikasikan ke Redis pub/sub
(untuk klien yang sedang terhubung). DB adalah kebenaran; Redis hanya transport.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, created_at_column, uuid_pk

if TYPE_CHECKING:
    from app.models.job import Job


class JobEvent(Base):
    """Satu kejadian dalam riwayat pemrosesan sebuah job."""

    __tablename__ = "job_events"

    id: Mapped[UUID] = uuid_pk()
    job_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Data tambahan, mis. {"progress": 40, "segment_id": "..."}.
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    job: Mapped[Job] = relationship(back_populates="events", lazy="raise")

    __table_args__ = (
        # TECH_SPEC §3: indeks wajib job_events(job_id, created_at)
        Index("ix_job_events_job_id_created_at", "job_id", "created_at"),
    )
