"""Model tabel ``overlay_assets`` dan ``segment_overlays`` — B-roll/overlay media.

Dua tabel, bukan satu, karena keduanya menjawab pertanyaan berbeda:

* ``overlay_assets`` — **apa** yang dimiliki pengguna: pustaka gambar/video
  ilustrasi yang diunggah sekali dan dapat dipakai berkali-kali. Termasuk
  efek suara untuk penanda ("efek ding", whoosh).
* ``segment_overlays`` — **di mana dan kapan** sebuah aset muncul pada klip
  tertentu. Satu baris = satu penempatan, dengan rentang waktu di dalam segmen.

Memisahkannya penting: menghapus satu penempatan tidak boleh menghapus berkas
yang dipakai di klip lain, dan menghapus aset harus membersihkan semua
penempatannya (CASCADE) agar tidak ada penempatan yang menunjuk berkas hilang.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, created_at_column, uuid_pk

if TYPE_CHECKING:
    from app.models.segment import Segment

#: Jenis aset yang didukung. "audio" dipisah karena tidak punya dimensi visual
#: dan tidak ikut compositing gambar — ia dimux ke trek suara.
OVERLAY_KINDS: tuple[str, ...] = ("image", "video", "audio")

#: Penempatan overlay di dalam frame. Ditulis sebagai konstanta agar API dan
#: worker memakai daftar yang sama.
OVERLAY_POSITIONS: tuple[str, ...] = (
    "top_left",
    "top_right",
    "bottom_left",
    "bottom_right",
    "center",
    "top_center",
    "bottom_center",
    "full",
)

#: Perilaku transisi masuk/keluar overlay.
OVERLAY_TRANSITIONS: tuple[str, ...] = ("none", "fade", "slide")


class OverlayAsset(Base):
    """Satu berkas B-roll/efek suara milik pengguna."""

    __tablename__ = "overlay_assets"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: image | video | audio
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Nama yang ditampilkan ke pengguna.
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Kata kunci pemicu, mis. ["grafik", "angka", "persen"]. Dipakai deteksi
    #: topik untuk mengusulkan overlay saat transkrip menyebut istilah ini.
    #: Disimpan sebagai teks dipisah koma (bukan array) agar sederhana untuk
    #: pencarian longgar dan cukup untuk skala MVP.
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    r2_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Durasi aset video/audio; NULL untuk gambar diam.
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Posisi bawaan saat aset ini diusulkan otomatis.
    default_position: Mapped[str] = mapped_column(String(32), nullable=False, default="top_right")
    #: Skala lebar aset relatif terhadap lebar klip (0.1–1.0).
    default_scale: Mapped[float] = mapped_column(Float, nullable=False, default=0.35)
    created_at: Mapped[datetime] = created_at_column()

    placements: Mapped[list[SegmentOverlay]] = relationship(
        back_populates="asset", cascade="all, delete-orphan", lazy="raise"
    )

    __table_args__ = (
        CheckConstraint(
            f"kind IN ({','.join(repr(k) for k in OVERLAY_KINDS)})", name="kind_valid"
        ),
        CheckConstraint(
            f"default_position IN ({','.join(repr(p) for p in OVERLAY_POSITIONS)})",
            name="default_position_valid",
        ),
        CheckConstraint("default_scale > 0 AND default_scale <= 1", name="scale_range"),
        Index("ix_overlay_assets_user_id", "user_id"),
    )


class SegmentOverlay(Base):
    """Penempatan satu aset overlay pada satu segmen klip."""

    __tablename__ = "segment_overlays"

    id: Mapped[UUID] = uuid_pk()
    segment_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("segments.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("overlay_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Waktu MULAI/MULAI relatif terhadap awal segmen (bukan media sumber).
    #: Relatif ke segmen supaya menggeser batas segmen tidak membuat overlay
    #: ikut bergeser — pengguna menempatkan overlay pada potongan itu, bukan
    #: pada menit ke-N video panjang.
    start_s: Mapped[float] = mapped_column(Float, nullable=False)
    end_s: Mapped[float] = mapped_column(Float, nullable=False)
    position: Mapped[str] = mapped_column(String(32), nullable=False, default="top_right")
    #: Lebar aset relatif terhadap lebar klip.
    scale: Mapped[float] = mapped_column(Float, nullable=False, default=0.35)
    #: 0–100.
    opacity: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    #: Transisi masuk/keluar.
    transition: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    #: Milidetik durasi transisi.
    transition_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    #: True bila dibuat oleh saran otomatis (bukan pengguna). Ditandai agar UI
    #: dapat menampilkan "usulan" dan pengguna tahu mana yang perlu ditinjau.
    suggested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = created_at_column()

    asset: Mapped[OverlayAsset] = relationship(back_populates="placements", lazy="raise")
    segment: Mapped[Segment] = relationship(lazy="raise")

    __table_args__ = (
        CheckConstraint("end_s > start_s", name="time_range_valid"),
        CheckConstraint(
            f"position IN ({','.join(repr(p) for p in OVERLAY_POSITIONS)})", name="position_valid"
        ),
        CheckConstraint("scale > 0 AND scale <= 1", name="scale_range"),
        CheckConstraint("opacity BETWEEN 0 AND 100", name="opacity_range"),
        CheckConstraint(
            f"transition IN ({','.join(repr(t) for t in OVERLAY_TRANSITIONS)})",
            name="transition_valid",
        ),
        Index("ix_segment_overlays_segment_id", "segment_id"),
    )
