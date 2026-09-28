"""Model tabel ``social_accounts`` (TECH_SPEC §3).

Kolom: id, user_id, platform, encrypted_token, refresh_token, expires_at, scopes
-- dibuat walau publish ditunda (D2), supaya skema tidak perlu migrasi besar
saat Sprint 4 dimulai.

KEAMANAN (TECH_SPEC §3): ``encrypted_token`` WAJIB AES-256-GCM dengan AAD via
``cryptography`` — bukan Fernet tanpa AAD. Enkripsi/dekripsi dilakukan di
``app/core/security.py`` (TokenCipher); kolom ini hanya menyimpan string base64
``nonce||ciphertext``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, UTCDateTime, created_at_column, uuid_pk

if TYPE_CHECKING:
    from app.models.scheduled_post import ScheduledPost


class SocialAccount(Base):
    """Akun platform sosial yang tertaut (TikTok/Meta), token terenkripsi."""

    __tablename__ = "social_accounts"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: "tiktok" | "youtube" | "instagram" — nilai bebas, divalidasi di service layer.
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    #: base64(nonce || ciphertext) AES-256-GCM. JANGAN pernah log kolom ini.
    encrypted_token: Mapped[str] = mapped_column(Text, nullable=False)
    #: Token refresh, dienkripsi dengan AAD field berbeda (lihat security.py).
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    #: Scope OAuth yang diberikan, dipisah spasi.
    scopes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    scheduled_posts: Mapped[list[ScheduledPost]] = relationship(
        back_populates="account", lazy="raise"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "platform", name="uq_social_accounts_user_id_platform"),
    )
