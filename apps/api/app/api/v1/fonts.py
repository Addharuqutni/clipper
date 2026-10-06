"""Router fonts: unggah dan daftar font kustom (.ttf/.otf) milik pengguna.

**Mengapa unggah font, bukan hanya daftar font sistem.** Font pilihan kreator
(mis. "Montserrat Black", "Bebas Neue") belum tentu terpasang di Windows. Tanpa
jalur unggah, pilihan font di UI diam-diam jatuh ke font pengganti dan hasil
render tidak sesuai pratinjau.

Font disimpan di ``<storage>/clipper-fonts/fonts/<user>/``; worker-render
memberikan folder itu ke libass lewat opsi ``fontsdir``.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.v1.auth import CurrentUserOrDev, DbSession
from app.models.font_asset import FontAsset

router = APIRouter()

logger = logging.getLogger(__name__)

#: Font TrueType dan OpenType saja. libass tidak dapat memuat WOFF/WOFF2, dan
#: menerimanya hanya akan menghasilkan kegagalan di tengah render.
ALLOWED_SUFFIXES = {".ttf", ".otf", ".ttc"}

#: Batas ukuran satu berkas font. Font terbesar yang lazim (CJK dengan banyak
#: glif) masih jauh di bawah ini; batas ini mencegah unggahan tak sengaja.
MAX_FONT_BYTES = 20 * 1024 * 1024

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class FontResponse(BaseModel):
    """Satu font kustom milik pengguna."""

    id: UUID
    #: Nama keluarga font seperti yang dikenal libass (dibaca dari berkas).
    family: str
    #: Nama berkas asli, untuk ditampilkan ke pengguna.
    original_filename: str
    size_bytes: int
    r2_key: str


class FontListResponse(BaseModel):
    """Daftar font milik pengguna."""

    items: list[FontResponse]
    total: int


def _safe_filename(name: str) -> str:
    """Bersihkan nama berkas agar aman dipakai sebagai object key.

    ``..`` dan pemisah jalur dibuang: nama berkas datang dari klien dan dapat
    berisi apa saja, termasuk ``../../etc/passwd``. Tanpa pembersihan ini,
    object key dapat menunjuk ke luar direktori penyimpanan.
    """
    base = Path(name).name
    cleaned = _SAFE_NAME.sub("_", base).strip("._") or "font"
    return cleaned[:120]


def _font_family(content: bytes, filename: str) -> str:
    """Baca nama keluarga font dari isi berkas.

    Memakai fontTools bila tersedia. Bila tidak, jatuh ke nama berkas TANPA
    ekstensi — libass mencocokkan nama keluarga, jadi tebakan dari nama berkas
    sering meleset, tetapi itu jauh lebih baik daripada menolak unggahan
    pengguna hanya karena satu pustaka opsional tidak terpasang.
    """
    try:
        from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

        with TTFont(io.BytesIO(content), fontNumber=0, lazy=True) as font:
            for record in font["name"].names:
                if record.nameID == 1:  # 1 = Font Family
                    value = str(record.toUnicode()).strip()
                    if value:
                        return value
    except Exception:  # noqa: BLE001 — pustaka opsional / berkas cacat
        # Tidak menggagalkan unggahan hanya karena fontTools absen atau berkas
        # tidak terbaca; nama berkas dipakai sebagai tebakan. Tetap dicatat
        # supaya "font selalu salah nama" bisa ditelusuri, bukan bisu.
        logger.warning("font_family_tidak_terbaca", extra={"berkas": filename}, exc_info=True)
    return Path(filename).stem


@router.get(
    "",
    response_model=FontListResponse,
    summary="Daftar font kustom milik pengguna",
)
async def list_fonts(current_user: CurrentUserOrDev, db: DbSession) -> FontListResponse:
    """Kembalikan font yang pernah diunggah pengguna."""
    rows = (
        await db.execute(
            select(FontAsset)
            .where(FontAsset.user_id == current_user.id)
            .order_by(FontAsset.created_at.asc())
        )
    ).scalars().all()

    return FontListResponse(
        items=[
            FontResponse(
                id=row.id,
                family=row.family,
                original_filename=row.original_filename,
                size_bytes=row.size_bytes,
                r2_key=row.r2_key,
            )
            for row in rows
        ],
        total=len(rows),
    )


@router.post(
    "",
    response_model=FontResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Unggah font kustom (.ttf/.otf)",
)
async def upload_font(
    current_user: CurrentUserOrDev,
    db: DbSession,
    file: Annotated[UploadFile, File()],
) -> FontResponse:
    """Terima satu berkas font, simpan ke storage, catat di basis data.

    Raises:
        HTTPException: 422 bila ekstensi/ukuran tidak sah; 409 bila nama
            keluarga sudah pernah diunggah (dua font dengan keluarga sama akan
            membuat pemilihan font tidak deterministik saat render).
    """
    filename = _safe_filename(file.filename or "font.ttf")
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Format '{suffix or 'tidak dikenal'}' tidak didukung. "
                f"Gunakan salah satu dari {sorted(ALLOWED_SUFFIXES)}."
            ),
        )

    content = await file.read(MAX_FONT_BYTES + 1)  # jangan baca berkas raksasa utuh ke RAM
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Berkas font kosong.",
        )
    if len(content) > MAX_FONT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Berkas {len(content) // 1024 // 1024} MB melebihi batas "
                f"{MAX_FONT_BYTES // 1024 // 1024} MB."
            ),
        )

    from clipper_shared.storage import store_font_bytes

    # Parsing font memblokir; jangan jalankan di event loop.
    family = await asyncio.to_thread(_font_family, content, filename)

    existing = (
        await db.execute(
            select(FontAsset).where(
                FontAsset.user_id == current_user.id, FontAsset.family == family
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Font dengan keluarga '{family}' sudah ada.",
        )

    # Ditulis SETELAH cek duplikat: font yang ditolak tidak meninggalkan berkas.
    r2_key, _ = await asyncio.to_thread(store_font_bytes, str(current_user.id), filename, content)
    asset = FontAsset(
        user_id=current_user.id,
        family=family,
        original_filename=filename,
        size_bytes=len(content),
        r2_key=r2_key,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)

    return FontResponse(
        id=asset.id,
        family=asset.family,
        original_filename=asset.original_filename,
        size_bytes=asset.size_bytes,
        r2_key=asset.r2_key,
    )


@router.delete(
    "/{font_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Hapus font kustom",
)
async def delete_font(
    font_id: UUID,
    current_user: CurrentUserOrDev,
    db: DbSession,
) -> None:
    """Hapus font milik pengguna."""
    asset = (
        await db.execute(
            select(FontAsset).where(
                FontAsset.id == font_id, FontAsset.user_id == current_user.id
            )
        )
    ).scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Font tidak ditemukan")

    from clipper_shared import storage as layout
    from clipper_shared.storage import delete_object

    await db.delete(asset)
    await db.commit()
    delete_object(layout.FONTS, asset.r2_key)
