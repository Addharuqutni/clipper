"""Model tabel ``renders`` (TECH_SPEC §3).

Kolom: id, segment_id, kind[preview|final], r2_key, preset, subtitle_style JSONB,
status, duration_ms, size_bytes.

``kind`` dibatasi ``preview``|``final`` karena dua jalur itu punya encoding
berbeda tajam (TECH_SPEC §4.3): preview 540×960 `-preset veryfast -crf 30`,
final `-preset slow -crf 18`.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from clipper_shared.job_state import JOB_STATUSES
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, created_at_column, uuid_pk

if TYPE_CHECKING:
    from app.models.scheduled_post import ScheduledPost
    from app.models.segment import Segment

#: TECH_SPEC §3: kind[preview|final]
RENDER_KINDS: tuple[str, ...] = ("preview", "final")

#: queued|running|done|failed|canceled — ``canceled`` = dihentikan pengguna,
#: bukan kegagalan (UI tidak menampilkannya sebagai "Render gagal").
#: Nilainya sama dengan status job, jadi diambil dari satu sumber
#: (:mod:`clipper_shared.job_state`) alih-alih ditulis ulang di sini.
RENDER_STATUSES: tuple[str, ...] = JOB_STATUSES

#: Mode reframing (lihat clipper_shared.reframe.CropMode). Disimpan per render
#: karena mode yang berbeda mengubah geometri keluaran secara fundamental, dan
#: pengguna berhak mengubahnya lalu me-render ulang tanpa kehilangan hasil lama.
CROP_MODES: tuple[str, ...] = ("face_track", "black_bars", "blurred_fill")

#: Mode bawaan bila pengguna tidak memilih.
DEFAULT_CROP_MODE = "face_track"


class Render(Base):
    """Satu eksekusi FFmpeg untuk satu segmen dengan satu gaya subtitle."""

    __tablename__ = "renders"

    id: Mapped[UUID] = uuid_pk()
    segment_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("segments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    r2_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Nama preset FFmpeg/encoding, mis. "preview-540x960".
    preset: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Gaya subtitle terpakai (warna, outline, box, posisi) — dibekukan saat render
    #: supaya mengubah preset belakangan tidak mengubah hasil yang sudah jadi.
    subtitle_style: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    #: Mode reframing yang dipakai render ini. Disimpan (bukan hanya diturunkan
    #: dari preset) supaya hasil lama tetap dapat direproduksi walau default
    #: global berubah, dan supaya UI dapat menampilkan mode tiap render.
    crop_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DEFAULT_CROP_MODE, server_default=DEFAULT_CROP_MODE
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    segment: Mapped[Segment] = relationship(back_populates="renders", lazy="raise")
    scheduled_posts: Mapped[list[ScheduledPost]] = relationship(
        back_populates="render", cascade="all, delete-orphan", lazy="raise"
    )

    __table_args__ = (
        CheckConstraint("kind IN ('preview', 'final')", name="kind_valid"),
        CheckConstraint("status IN ('queued', 'running', 'done', 'failed', 'canceled')", name="status_valid"),
        CheckConstraint(
            "crop_mode IN ('face_track', 'black_bars', 'blurred_fill')",
            name="crop_mode_valid",
        ),
    )
