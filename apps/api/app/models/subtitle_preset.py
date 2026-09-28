"""Model tabel ``subtitle_presets`` (TECH_SPEC §3).

Kolom: id, user_id, name, style JSONB  -- warna, outline, box, posisi
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, created_at_column, uuid_pk

if TYPE_CHECKING:
    pass


class SubtitlePreset(Base):
    """Gaya subtitle karaoke milik user (dipakai saat render).

    ``style`` JSONB menyimpan warna/outline/box/posisi. Gaya ini diteruskan ke
    FFmpeg ``subtitles=`` filter dengan force_style, dan dikompilasi ke ASS.
    """

    __tablename__ = "subtitle_presets"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    style: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_subtitle_presets_user_id_name"),)
