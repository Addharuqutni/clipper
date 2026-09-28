"""Router overlay: pustaka B-roll/efek suara dan penempatannya pada segmen.

Tiga kemampuan yang diberikan di sini:

1. **Pustaka aset** — unggah gambar/video/audio ilustrasi beserta kata kunci
   pemicunya.
2. **Penempatan manual** — atur kapan sebuah aset muncul di dalam sebuah segmen,
   di posisi mana, sebesar apa, dan dengan transisi apa.
3. **Usulan otomatis** — cari kata kunci aset di dalam kata-kata transkrip
   segmen, lalu usulkan penempatan. Usulan ditandai ``suggested`` sehingga UI
   dapat membedakannya dari yang dibuat pengguna.

**Mengapa usulan berbasis kata kunci, bukan model.** Deteksi topik dengan LLM
menambah satu panggilan berbayar dan satu titik kegagalan untuk manfaat yang
belum terbukti pada MVP. Pencocokan kata kunci yang dipilih pengguna sendiri
dapat diprediksi, gratis, dan bekerja tanpa penyedia AI aktif — dan pengguna
selalu dapat menyunting hasilnya.

Logika non-HTTP (kepemilikan, validasi, algoritme usulan) ada di
:mod:`app.services.overlay`.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, UploadFile, status
from pydantic import BaseModel, Field

from app.api.v1.auth import CurrentUserOrDev, DbSession
from app.models.overlay import OVERLAY_KINDS, OVERLAY_POSITIONS, OVERLAY_TRANSITIONS
from app.services import overlay as overlay_service
from app.services.overlay import MAX_OVERLAY_BYTES

router = APIRouter()


class OverlayAssetResponse(BaseModel):
    """Satu aset B-roll/efek suara."""

    id: UUID
    kind: str
    name: str
    tags: list[str]
    size_bytes: int
    duration_s: float | None
    default_position: str
    default_scale: float


class OverlayAssetListResponse(BaseModel):
    """Daftar aset milik pengguna."""

    items: list[OverlayAssetResponse]
    total: int


class PlacementResponse(BaseModel):
    """Satu penempatan overlay pada segmen."""

    id: UUID
    segment_id: UUID
    asset_id: UUID
    asset_name: str
    asset_kind: str
    start_s: float
    end_s: float
    position: str
    scale: float
    opacity: int
    transition: str
    transition_ms: int
    #: True bila ini usulan otomatis yang belum dikonfirmasi pengguna.
    suggested: bool


class PlacementListResponse(BaseModel):
    """Daftar penempatan overlay untuk sebuah segmen."""

    items: list[PlacementResponse]
    total: int


class PlacementCreateRequest(BaseModel):
    """Payload penempatan overlay baru."""

    asset_id: UUID
    start_s: float = Field(..., ge=0)
    end_s: float = Field(..., gt=0)
    position: str | None = None
    scale: float | None = Field(default=None, gt=0, le=1)
    opacity: int = Field(default=100, ge=0, le=100)
    transition: str = "none"
    transition_ms: int = Field(default=300, ge=0, le=5000)


class PlacementUpdateRequest(BaseModel):
    """Perubahan penempatan. Field yang tidak dikirim tidak diubah."""

    start_s: float | None = Field(default=None, ge=0)
    end_s: float | None = Field(default=None, gt=0)
    position: str | None = None
    scale: float | None = Field(default=None, gt=0, le=1)
    opacity: int | None = Field(default=None, ge=0, le=100)
    transition: str | None = None
    transition_ms: int | None = Field(default=None, ge=0, le=5000)


class SuggestResponse(BaseModel):
    """Hasil usulan otomatis untuk sebuah segmen."""

    segment_id: UUID
    created: list[PlacementResponse]
    #: Kata kunci yang cocok, untuk menjelaskan MENGAPA usulan ini muncul.
    matched_tags: list[str]


class OverlayOptionsResponse(BaseModel):
    """Pilihan yang sah untuk UI."""

    kinds: list[str]
    positions: list[str]
    transitions: list[str]
    max_bytes: int


def _asset_response(asset: object) -> OverlayAssetResponse:
    return OverlayAssetResponse(**overlay_service.asset_fields(asset))  # type: ignore[arg-type]


def _placement_response(placement: object, asset: object) -> PlacementResponse:
    return PlacementResponse(**overlay_service.placement_fields(placement, asset))  # type: ignore[arg-type]


@router.get(
    "/options",
    response_model=OverlayOptionsResponse,
    summary="Pilihan overlay yang sah untuk UI",
)
async def overlay_options() -> OverlayOptionsResponse:
    """Daftar jenis aset, posisi, dan transisi — dikirim agar UI tidak menebak."""
    return OverlayOptionsResponse(
        kinds=list(OVERLAY_KINDS),
        positions=list(OVERLAY_POSITIONS),
        transitions=list(OVERLAY_TRANSITIONS),
        max_bytes=MAX_OVERLAY_BYTES,
    )


@router.get(
    "/assets",
    response_model=OverlayAssetListResponse,
    summary="Daftar aset B-roll/efek suara milik pengguna",
)
async def list_assets(
    current_user: CurrentUserOrDev, db: DbSession
) -> OverlayAssetListResponse:
    """Kembalikan pustaka aset pengguna, terbaru dulu."""
    rows = await overlay_service.list_assets(db, current_user.id)
    return OverlayAssetListResponse(items=[_asset_response(row) for row in rows], total=len(rows))


@router.post(
    "/assets",
    response_model=OverlayAssetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Unggah aset B-roll/efek suara",
)
async def upload_asset(
    current_user: CurrentUserOrDev,
    db: DbSession,
    file: Annotated[UploadFile, File()],
    name: Annotated[str, Form()],
    tags: Annotated[str, Form()] = "",
    default_position: Annotated[str, Form()] = "top_right",
    default_scale: Annotated[float, Form()] = 0.35,
) -> OverlayAssetResponse:
    """Terima satu berkas aset beserta metadata pemicunya.

    ``tags`` adalah kata kunci yang memicu usulan otomatis ketika muncul di
    transkrip. Contoh: untuk gambar grafik, ``"grafik,angka,persen,statistik"``.

    Raises:
        HTTPException: 422 bila jenis berkas/ukuran/posisi tidak sah.
    """
    kind = overlay_service.validate_upload(
        content_type=file.content_type or "",
        name=name,
        default_position=default_position,
        default_scale=default_scale,
    )
    # Batas dibaca lebih dulu (MAX+1) agar berkas raksasa tidak pernah masuk RAM utuh.
    content = await file.read(MAX_OVERLAY_BYTES + 1)
    overlay_service.validate_content_size(content)

    asset = await overlay_service.create_asset(
        db,
        current_user.id,
        kind=kind,
        name=name,
        filename=file.filename or f"overlay.{kind}",
        content_type=file.content_type,
        tags=tags,
        default_position=default_position,
        default_scale=default_scale,
        content=content,
    )
    return _asset_response(asset)


@router.delete(
    "/assets/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Hapus aset overlay",
)
async def delete_asset(
    asset_id: UUID, current_user: CurrentUserOrDev, db: DbSession
) -> None:
    """Hapus aset beserta SEMUA penempatannya (CASCADE di basis data)."""
    await overlay_service.delete_asset(db, current_user.id, asset_id)


@router.get(
    "/segments/{segment_id}",
    response_model=PlacementListResponse,
    summary="Daftar penempatan overlay pada sebuah segmen",
)
async def list_placements(
    segment_id: UUID, current_user: CurrentUserOrDev, db: DbSession
) -> PlacementListResponse:
    """Kembalikan penempatan overlay segmen, terurut waktu mulai."""
    rows = await overlay_service.list_placements(db, current_user.id, segment_id)
    return PlacementListResponse(items=[_placement_response(p, a) for p, a in rows], total=len(rows))


@router.post(
    "/segments/{segment_id}",
    response_model=PlacementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tempatkan overlay pada sebuah segmen",
)
async def create_placement(
    segment_id: UUID,
    payload: PlacementCreateRequest,
    current_user: CurrentUserOrDev,
    db: DbSession,
) -> PlacementResponse:
    """Tempatkan aset pada rentang waktu di dalam segmen.

    Raises:
        HTTPException: 422 bila rentang waktu di luar durasi segmen.
    """
    placement, asset = await overlay_service.create_placement(
        db,
        current_user.id,
        segment_id=segment_id,
        asset_id=payload.asset_id,
        start_s=payload.start_s,
        end_s=payload.end_s,
        position=payload.position,
        scale=payload.scale,
        opacity=payload.opacity,
        transition=payload.transition,
        transition_ms=payload.transition_ms,
    )
    return _placement_response(placement, asset)


@router.patch(
    "/placements/{placement_id}",
    response_model=PlacementResponse,
    summary="Ubah penempatan overlay",
)
async def update_placement(
    placement_id: UUID,
    payload: PlacementUpdateRequest,
    current_user: CurrentUserOrDev,
    db: DbSession,
) -> PlacementResponse:
    """Perbarui penempatan. Menyunting usulan otomatis menandainya sebagai milik pengguna."""
    placement, asset = await overlay_service.update_placement(
        db, current_user.id, placement_id, payload.model_dump(exclude_none=True)
    )
    return _placement_response(placement, asset)


@router.delete(
    "/placements/{placement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Hapus penempatan overlay",
)
async def delete_placement(
    placement_id: UUID, current_user: CurrentUserOrDev, db: DbSession
) -> None:
    """Hapus satu penempatan. Asetnya tetap ada di pustaka."""
    await overlay_service.delete_placement(db, current_user.id, placement_id)


@router.post(
    "/segments/{segment_id}/suggest",
    response_model=SuggestResponse,
    summary="Usulkan penempatan overlay dari kata kunci transkrip",
)
async def suggest_placements(
    segment_id: UUID, current_user: CurrentUserOrDev, db: DbSession
) -> SuggestResponse:
    """Cari tag aset di dalam kata-kata segmen, lalu usulkan penempatan.

    Aturan usulan (lihat :func:`app.services.overlay.suggest_for_segment`):
    pencocokan **kata utuh**, sekali per aset, durasi bawaan 3 detik, dan
    usulan yang bertabrakan digeser — bukan ditumpuk.
    """
    segment, created, assets, matched_tags = await overlay_service.suggest_placements(
        db, current_user.id, segment_id
    )
    asset_by_id = {a.id: a for a in assets}
    return SuggestResponse(
        segment_id=segment.id,
        created=[_placement_response(p, asset_by_id[p.asset_id]) for p in created],
        matched_tags=matched_tags,
    )
