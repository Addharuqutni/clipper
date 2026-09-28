"""Model tabel ``transcripts`` (TECH_SPEC §3).

Kolom: id, job_id, language, words JSONB, speakers JSONB, full_text, model_used.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, created_at_column, uuid_pk

if TYPE_CHECKING:
    from app.models.job import Job


class Transcript(Base):
    """Hasil STT satu job (satu baris per bahasa)."""

    __tablename__ = "transcripts"

    id: Mapped[UUID] = uuid_pk()
    job_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: ISO-639-1, mis. "id", "en".
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Daftar kata bertimestamp (word-level) — sumber karaoke subtitle.
    words: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    #: Hasil diarisasi (boleh kosong di MVP, TECH_SPEC §5.2).
    speakers: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    full_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: mis. "small/int8" atau "whisper-1" — jejak backend yang dipakai (§5.1).
    model_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    job: Mapped[Job] = relationship(back_populates="transcripts", lazy="raise")
