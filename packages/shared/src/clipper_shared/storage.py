"""Tata letak penyimpanan di disk, dipakai bersama API dan worker.

Semua berkas berada di bawah ``LOCAL_STORAGE_DIR`` (bawaan ``<repo>/output``)::

    clipper-raw/raw/...          media sumber
    clipper-renders/renders/...  hasil render
    clipper-overlays/overlays/.. aset B-roll / efek suara
    clipper-fonts/fonts/...      font kustom
    uploads/<upload_id>/<n>      potongan unggahan yang belum selesai
    clipper.db                   basis data SQLite

Nama folder ``clipper-*`` dipertahankan dari versi sebelumnya supaya data dan
kolom ``r2_key`` yang sudah tersimpan tetap menunjuk berkas yang benar.
"""

from __future__ import annotations

import os
from pathlib import Path

RAW = "clipper-raw"
RENDERS = "clipper-renders"
OVERLAYS = "clipper-overlays"
FONTS = "clipper-fonts"


def repo_root() -> Path:
    """Akar repositori (packages/shared/src/clipper_shared -> naik 4 tingkat)."""
    return Path(__file__).resolve().parents[4]


def storage_root() -> Path:
    """Akar penyimpanan. Jalur relatif ditafsirkan terhadap akar repo."""
    root = Path(os.getenv("LOCAL_STORAGE_DIR") or "output")
    return root if root.is_absolute() else repo_root() / root


def object_path(bucket: str, key: str) -> Path:
    """Petakan ``(bucket, key)`` ke jalur berkas.

    Komponen ``..`` dan kosong dibuang: ``key`` berasal dari basis data, dan
    tanpa pembersihan sebuah nilai dapat menunjuk ke luar akar penyimpanan.
    """
    parts = [part for part in key.replace("\\", "/").split("/") if part not in {"", ".", ".."}]
    if not parts:
        raise ValueError(f"object key kosong: {key!r}")
    return storage_root() / bucket / Path(*parts)


def uploads_dir() -> Path:
    """Direktori potongan unggahan yang belum selesai."""
    return storage_root() / "uploads"


def youtube_cookies_path() -> Path:
    """Cookies YouTube terenkripsi milik pengguna (satu pengguna, satu berkas)."""
    return storage_root() / "secrets" / "youtube_cookies.enc"


def repo_path(env_name: str, default_relative: str) -> Path:
    """Jalur dari env ``env_name``, atau ``<repo>/<default_relative>``.

    Jalur relatif (di env maupun bawaan) ditafsirkan terhadap akar repo, bukan
    direktori kerja proses — uvicorn dijalankan dari ``apps/api``.
    """
    path = Path(os.getenv(env_name) or default_relative)
    return path if path.is_absolute() else repo_root() / path


def binary(name: str) -> str:
    """Jalur program eksternal (``ffmpeg``, ``ffprobe``).

    Urutan: env ``<NAME>_BINARY`` → ``<repo>/.libs/ffmpeg/<name>.exe`` (dipasang
    ``start.cmd``) → ``PATH``. Bila tidak ada sama sekali, nama polos
    dikembalikan supaya pesan gagalnya datang dari sistem operasi.
    """
    import shutil

    override = os.getenv(f"{name.upper()}_BINARY")
    if override:
        return override
    bundled = repo_root() / ".libs" / "ffmpeg" / f"{name}.exe"
    if bundled.is_file():
        return str(bundled)
    return shutil.which(name) or name
