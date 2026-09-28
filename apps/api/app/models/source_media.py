"""Model tabel ``source_media`` (TECH_SPEC §3).

Kolom: id, job_id, r2_key, size_bytes, duration_s, codec, width, height,
upload_id, expires_at.

``upload_id`` menyimpan S3 multipart upload id agar upload >1 GB bisa di-resume.
``expires_at`` adalah basis Celery beat ``purge_expired_raw_media``: PRD meminta
hapus "48 jam setelah proses pemotongan selesai", yang bergantung kejadian —
lifecycle rule R2 (berbasis umur objek) tidak cukup, jadi kolom inilah yang
menentukan (TECH_SPEC §3 catatan lifecycle).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, UTCDateTime, created_at_column, uuid_pk

if TYPE_CHECKING:
    from app.models.job import Job


class SourceMedia(Base):
    """Metadata file media mentah di object storage."""

    __tablename__ = "source_media"

    id: Mapped[UUID] = uuid_pk()
    job_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    #: Object key di bucket raw (R2 di prod, MinIO di dev).
    r2_key: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    codec: Mapped[str | None] = mapped_column(String(64), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: S3 multipart upload id — NULL bila upload belum dimulai/dibatalkan.
    upload_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    #: Nama berkas asli dari klien, dipakai untuk pesan error dan unduhan.
    original_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Kode bahasa (BCP-47) media sumber dideteksi, mis. "id" atau "en".
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Dari mana transkrip diperoleh: "whisper" atau trek subtitle YouTube.
    #: Dicatat karena menentukan tingkat keyakinan timestamp per kata, dan
    #: karena melewati Whisper mengubah biaya maupun waktu proses secara drastis.
    transcript_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: Kredensial sesi YouTube pengguna, TERENKRIPSI (AES-256-GCM, TECH_SPEC §3).
    #: Disimpan terpisah dari social_accounts karena masa pakainya berbeda:
    #: cookies ini dihapus setelah job selesai, bukan disimpan untuk publikasi.
    youtube_cookies_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Kapan raw media boleh dihapus (TECH_SPEC §3 lifecycle).
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    job: Mapped[Job] = relationship(back_populates="source_media", lazy="raise")

    __table_args__ = (
        # Nilai dibatasi agar kolom ini tidak diam-diam menerima nilai tak dikenal
        # yang lalu menyesatkan analitik biaya.
        CheckConstraint(
            "transcript_source IS NULL OR transcript_source IN ('whisper', 'youtube_subtitle')",
            name="transcript_source_valid",
        ),
    )
