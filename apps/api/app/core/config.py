"""Konfigurasi API.

``.env`` di akar repo dimuat ke **environment proses** (tanpa menimpa variabel
yang sudah ada), bukan hanya ke objek :class:`Settings`. Worker berjalan di
proses yang sama dan membaca ``os.environ`` lewat ``clipper_shared``; bila
``.env`` hanya masuk ke ``Settings``, API dan worker bisa memakai nilai berbeda
(mis. lokasi penyimpanan atau kunci enkripsi) saat uvicorn dijalankan tanpa
``scripts\\run-api.cmd``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: apps/api/app/core/config.py -> naik 4 tingkat = akar repo.
_REPO_ENV = Path(__file__).resolve().parents[4] / ".env"
if _REPO_ENV.is_file():
    load_dotenv(_REPO_ENV, override=False)


class Settings(BaseSettings):
    """Setting runtime. Nama field = nama env var, case-insensitive."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    APP_NAME: str = "ClipperAI API"
    ENV: Literal["dev", "staging", "prod"] = "dev"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"
    #: Origin frontend Next.js untuk CORS.
    CORS_ORIGINS: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000"])
    #: Host yang boleh dipakai di header ``Host``. Menolak host lain menutup
    #: serangan DNS rebinding dari halaman web jahat ke API lokal tanpa auth.
    ALLOWED_HOSTS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "[::1]"]
    )

    DB_ECHO: bool = False

    #: 32 byte base64 untuk AES-256-GCM (kunci API penyedia AI, cookies YouTube).
    TOKEN_ENCRYPTION_KEY: str = ""

    #: Batas ukuran unggahan (PRD FR-1.2: 3 GB).
    MAX_UPLOAD_BYTES: int = 3 * 1024**3
    #: Ukuran potongan unggahan; klien mengirim berkas per potongan ini.
    UPLOAD_PART_BYTES: int = 10 * 1024**2

    #: Interval pemeliharaan berkala (hapus media mentah kedaluwarsa).
    MAINTENANCE_INTERVAL_S: int = 3600

    @field_validator("CORS_ORIGINS", "ALLOWED_HOSTS", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Terima ``A,B`` dari env, bukan hanya JSON list."""
        if isinstance(value, str) and not value.startswith("["):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def database_url(self) -> str:
        """URL SQLAlchemy async ke berkas SQLite bersama worker."""
        from clipper_shared.db import get_sqlite_path

        path = get_sqlite_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{path.resolve().as_posix()}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cache setting agar env dibaca sekali per proses."""
    return Settings()


settings = get_settings()
