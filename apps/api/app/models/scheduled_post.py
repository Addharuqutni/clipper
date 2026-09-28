"""Model tabel ``scheduled_posts`` (TECH_SPEC §3).

Kolom: id, user_id, render_id, platform, caption, hashtags, scheduled_at, status
-- schema-ready, belum aktif (konsekuensi D2: publishing ditunda).

Tabel ini sengaja dibuat sekarang meski fiturnya belum ada, supaya skema DB
stabil dan Sprint 4 tidak memerlukan migrasi yang menyentuh tabel inti.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, UTCDateTime, created_at_column, uuid_pk

if TYPE_CHECKING:
    from app.models.render import Render
    from app.models.social_account import SocialAccount


class ScheduledPost(Base):
    """Rencana publikasi klip ke platform sosial (belum diaktifkan di MVP)."""

    __tablename__ = "scheduled_posts"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    render_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("renders.id", ondelete="CASCADE"),
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Akun sosial yang dipakai memposting. NULLABLE karena draf boleh dibuat
    #: sebelum pengguna menyambungkan akun; scheduler baru mewajibkannya saat
    #: status berubah menjadi `scheduled`.
    account_id: Mapped[UUID | None] = mapped_column(
        GUID(),
        ForeignKey("social_accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Hashtag dipisah spasi (string, bukan array) agar seragam dengan caption.
    hashtags: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    #: draft|scheduled|published|failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    created_at: Mapped[datetime] = created_at_column()

    render: Mapped[Render] = relationship(back_populates="scheduled_posts", lazy="raise")
    #: `ondelete="SET NULL"` pada kolom di atas berarti mencabut akun sosial
    #: tidak menghapus riwayat posting — hanya melepas kaitannya. Riwayat
    #: publikasi tetap berguna walau akunnya sudah tidak tersambung.
    account: Mapped[SocialAccount | None] = relationship(lazy="raise")

    __table_args__ = (
        # Scheduler (Sprint 4) akan selalu menyapu berdasarkan waktu + status.
        Index("ix_scheduled_posts_scheduled_at_status", "scheduled_at", "status"),
    )
