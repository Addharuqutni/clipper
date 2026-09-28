"""Model tabel ``font_assets`` — font kustom yang diunggah pengguna.

Dibuat sebagai tabel tersendiri (bukan kolom di ``users``) karena seorang
pengguna dapat mengunggah banyak font, dan tiap font perlu metadata sendiri
(nama keluarga, ukuran, lokasi objek).

``family`` adalah nama keluarga font seperti yang dikenal libass — inilah nilai
yang disimpan di ``SubtitleStyle.font_name``. Menyimpannya terpisah dari nama
berkas penting karena keduanya sering berbeda: ``Montserrat-Black.ttf`` memberi
keluarga ``Montserrat Black``, dan libass mencocokkan berdasarkan keluarga.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, created_at_column, uuid_pk

if TYPE_CHECKING:
    pass


class FontAsset(Base):
    """Satu berkas font kustom milik pengguna."""

    __tablename__ = "font_assets"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Nama keluarga font (dipakai libass), mis. "Montserrat Black".
    family: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Nama berkas asli dari pengguna, untuk ditampilkan di UI.
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Lokasi di object storage, mis. "fonts/<user_id>/<file>.ttf".
    r2_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        # Dua font dengan keluarga sama membuat pemilihan font saat render tidak
        # deterministik — libass akan memakai yang ditemukan lebih dulu, dan
        # urutannya bergantung pada isi direktori font.
        UniqueConstraint("user_id", "family", name="uq_font_assets_user_id_family"),
    )
