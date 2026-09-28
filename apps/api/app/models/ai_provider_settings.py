"""Model tabel ``ai_provider_settings`` — pengaturan penyedia AI per pengguna.

**Mengapa per pengguna, bukan global.** Dua alasan yang keduanya praktis:

1. **Biaya ditanggung pengguna.** Kuota penyedia ada di akun mereka, jadi
   kredensial pun milik mereka. Menyimpannya global berarti semua pengguna
   berbagi satu kuota dan satu tagihan.
2. **Kebutuhan berbeda.** Rekaman internal perusahaan tidak boleh dikirim ke
   penyedia publik; pengguna seperti itu menunjuk endpoint sendiri atau model
   lokal. Pengguna lain cukup memakai penyedia bawaan.

**Keamanan ``api_key_encrypted``.** Kunci API adalah kredensial penuh — siapa
pun yang memegangnya dapat memakai kuota pengguna, dan sering kali membaca
riwayat. Karena itu ia disimpan terenkripsi AES-256-GCM (TECH_SPEC §3) dan
**tidak pernah** dikembalikan API dalam bentuk apa pun. Yang dikembalikan ke UI
hanya penanda bahwa kunci sudah diisi, sehingga pengguna tahu keadaannya tanpa
nilainya pernah meninggalkan server.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, created_at_column, updated_at_column, uuid_pk

if TYPE_CHECKING:
    from app.models.user import User

#: Preset yang diizinkan. Harus sejalan dengan ``clipper_shared.ai_provider``.
PROVIDER_PRESETS: tuple[str, ...] = (
    "gemini",
    "openai",
    "anthropic",
    "openrouter",
    "groq",
    "together",
    "ollama",
    "custom",
)


class AiProviderSettings(Base):
    """Penyedia AI pilihan pengguna untuk skoring viral."""

    __tablename__ = "ai_provider_settings"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        # Satu pengguna punya satu pengaturan aktif. Constraint unik ini yang
        # menegakkannya di tingkat basis data — bukan hanya di kode — sehingga
        # dua permintaan bersamaan tidak dapat menghasilkan baris ganda.
        unique=True,
    )
    preset: Mapped[str] = mapped_column(String(32), nullable=False, default="gemini")
    #: Kosong = pakai bawaan preset (diisi saat penyedia dipanggil).
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    #: Kunci API TERENKRIPSI (AES-256-GCM, base64). NULL = belum diisi.
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Untuk Ollama atau model di jaringan sendiri.
    allow_private_host: Mapped[bool] = mapped_column(nullable=False, default=False)
    #: Teks arahan tetap yang ditambahkan ke setiap permintaan skoring.
    #: Disimpan di sini karena gaya penyuntingan biasanya konsisten antar-video.
    default_direction: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Jendela konteks model (token), dideteksi dari ``GET /models`` saat
    #: menyimpan atau diisi pengguna. NULL = tidak diketahui → cadangan
    #: ``clipper_shared.ai_provider.DEFAULT_CONTEXT_TOKENS``. Menentukan durasi
    #: video maksimum yang bisa dianalisis.
    context_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

    user: Mapped[User] = relationship(lazy="raise")

    __table_args__ = (
        CheckConstraint(
            "preset IN ('gemini','openai','anthropic','openrouter','groq',"
            "'together','ollama','custom')",
            name="preset_valid",
        ),
    )
