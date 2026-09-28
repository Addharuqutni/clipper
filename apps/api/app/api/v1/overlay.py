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
"""

from __future__ import annotations

import re
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.v1.auth import CurrentUserOrDev, DbSession
from app.models.job import Job
from app.models.overlay import OVERLAY_KINDS, OVERLAY_POSITIONS, OVERLAY_TRANSITIONS
from app.models.overlay import OverlayAsset as OverlayAssetModel
from app.models.overlay import SegmentOverlay as SegmentOverlayModel
from app.models.segment import Segment
from app.models.transcript import Transcript

router = APIRouter()

#: Jenis konten yang diterima, dipetakan ke ``kind``.
_CONTENT_KIND = (
    ("image/", "image"),
    ("video/", "video"),
    ("audio/", "audio"),
)

#: Batas ukuran satu aset overlay. B-roll adalah klip pendek/grafik; berkas
#: sebesar film panjang menandakan pengguna salah memilih berkas.
MAX_OVERLAY_BYTES = 50 * 1024 * 1024


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


def _asset_tags(raw: str | None) -> list[str]:
    """Ubah teks tag dipisah koma menjadi daftar bersih."""
    if not raw:
        return []
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


def _kind_for(content_type: str) -> str | None:
    """Tentukan ``kind`` dari content type."""
    lowered = (content_type or "").lower()
    for prefix, kind in _CONTENT_KIND:
        if lowered.startswith(prefix):
            return kind
    return None



async def _get_owned_segment(db: DbSession, user_id: Any, segment_id: UUID) -> Segment:
    """Ambil segmen milik user (lewat job) atau 404.

    Kepemilikan diperiksa melalui join ke ``jobs``: segmen tidak menyimpan
    ``user_id`` sendiri, dan mempercayai ``segment_id`` dari klien tanpa
    memeriksa pemiliknya akan membocorkan data antar-pengguna.
    """
    row = (
        await db.execute(
            select(Segment)
            .join(Job, Job.id == Segment.job_id)
            .where(Segment.id == segment_id, Job.user_id == user_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Segmen tidak ditemukan")
    return row


async def _get_owned_asset(db: DbSession, user_id: Any, asset_id: UUID) -> OverlayAssetModel:
    """Ambil aset overlay milik user atau 404."""
    row = (
        await db.execute(
            select(OverlayAssetModel).where(
                OverlayAssetModel.id == asset_id, OverlayAssetModel.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aset tidak ditemukan")
    return row


def _placement_response(placement: SegmentOverlayModel, asset: OverlayAssetModel) -> PlacementResponse:
    """Konversi ORM → DTO penempatan."""
    return PlacementResponse(
        id=placement.id,
        segment_id=placement.segment_id,
        asset_id=placement.asset_id,
        asset_name=asset.name,
        asset_kind=asset.kind,
        start_s=placement.start_s,
        end_s=placement.end_s,
        position=placement.position,
        scale=placement.scale,
        opacity=placement.opacity,
        transition=placement.transition,
        transition_ms=placement.transition_ms,
        suggested=placement.suggested,
    )


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
    rows = (
        await db.execute(
            select(OverlayAssetModel)
            .where(OverlayAssetModel.user_id == current_user.id)
            .order_by(OverlayAssetModel.created_at.desc())
        )
    ).scalars().all()

    return OverlayAssetListResponse(
        items=[
            OverlayAssetResponse(
                id=row.id,
                kind=row.kind,
                name=row.name,
                tags=_asset_tags(row.tags),
                size_bytes=row.size_bytes,
                duration_s=row.duration_s,
                default_position=row.default_position,
                default_scale=row.default_scale,
            )
            for row in rows
        ],
        total=len(rows),
    )


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
    if default_position not in OVERLAY_POSITIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Posisi '{default_position}' tidak dikenal; pilih salah satu {list(OVERLAY_POSITIONS)}",
        )
    if not 0 < default_scale <= 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="default_scale harus lebih dari 0 dan paling besar 1.",
        )

    kind = _kind_for(file.content_type or "")
    if kind is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Jenis berkas '{file.content_type or 'tidak dikenal'}' tidak didukung. "
                "Gunakan gambar (image/*), video (video/*), atau audio (audio/*)."
            ),
        )

    content = await file.read(MAX_OVERLAY_BYTES + 1)  # jangan baca berkas raksasa utuh ke RAM
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Berkas kosong."
        )
    if len(content) > MAX_OVERLAY_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Berkas {len(content) // 1024 // 1024} MB melebihi batas "
                f"{MAX_OVERLAY_BYTES // 1024 // 1024} MB."
            ),
        )

    import asyncio

    from app.core.storage import store_overlay_bytes

    r2_key, _ = await asyncio.to_thread(
        store_overlay_bytes, str(current_user.id), file.filename or f"overlay.{kind}", content
    )

    asset = OverlayAssetModel(
        user_id=current_user.id,
        kind=kind,
        name=name.strip()[:200] or (file.filename or "overlay")[:200],
        tags=",".join(_asset_tags(tags)) or None,
        r2_key=r2_key,
        content_type=file.content_type,
        size_bytes=len(content),
        default_position=default_position,
        default_scale=default_scale,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)

    return OverlayAssetResponse(
        id=asset.id,
        kind=asset.kind,
        name=asset.name,
        tags=_asset_tags(asset.tags),
        size_bytes=asset.size_bytes,
        duration_s=asset.duration_s,
        default_position=asset.default_position,
        default_scale=asset.default_scale,
    )


@router.delete(
    "/assets/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Hapus aset overlay",
)
async def delete_asset(
    asset_id: UUID, current_user: CurrentUserOrDev, db: DbSession
) -> None:
    """Hapus aset beserta SEMUA penempatannya (CASCADE di basis data)."""
    from clipper_shared import storage as layout

    from app.core.storage import delete_object

    asset = await _get_owned_asset(db, current_user.id, asset_id)
    await db.delete(asset)
    await db.commit()
    delete_object(layout.OVERLAYS, asset.r2_key)


@router.get(
    "/segments/{segment_id}",
    response_model=PlacementListResponse,
    summary="Daftar penempatan overlay pada sebuah segmen",
)
async def list_placements(
    segment_id: UUID, current_user: CurrentUserOrDev, db: DbSession
) -> PlacementListResponse:
    """Kembalikan penempatan overlay segmen, terurut waktu mulai."""
    segment = await _get_owned_segment(db, current_user.id, segment_id)

    rows = (
        await db.execute(
            select(SegmentOverlayModel, OverlayAssetModel)
            .join(OverlayAssetModel, OverlayAssetModel.id == SegmentOverlayModel.asset_id)
            .where(SegmentOverlayModel.segment_id == segment.id)
            .order_by(SegmentOverlayModel.start_s.asc())
        )
    ).all()

    return PlacementListResponse(
        items=[_placement_response(p, a) for p, a in rows],
        total=len(rows),
    )


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
    segment = await _get_owned_segment(db, current_user.id, segment_id)
    asset = await _get_owned_asset(db, current_user.id, payload.asset_id)

    segment_duration = segment.end_s - segment.start_s
    if payload.end_s > segment_duration + 0.01:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Overlay berakhir pada {payload.end_s:.1f}s, sedangkan segmen "
                f"hanya {segment_duration:.1f}s. Waktu dihitung dari awal segmen."
            ),
        )
    if payload.end_s <= payload.start_s:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Waktu akhir harus setelah waktu mulai.",
        )
    if payload.position is not None and payload.position not in OVERLAY_POSITIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Posisi '{payload.position}' tidak dikenal.",
        )
    if payload.transition not in OVERLAY_TRANSITIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Transisi '{payload.transition}' tidak dikenal.",
        )

    placement = SegmentOverlayModel(
        segment_id=segment.id,
        asset_id=asset.id,
        start_s=payload.start_s,
        end_s=payload.end_s,
        # Bawaan diambil dari ASET, bukan konstanta: pengguna menyetel preferensi
        # posisi saat mengunggah, dan mengabaikannya akan memaksa ia mengaturnya
        # ulang di setiap penempatan.
        position=payload.position or asset.default_position,
        scale=payload.scale if payload.scale is not None else asset.default_scale,
        opacity=payload.opacity,
        transition=payload.transition,
        transition_ms=payload.transition_ms,
        suggested=False,
    )
    db.add(placement)
    await db.commit()
    await db.refresh(placement)

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
    placement = (
        await db.execute(
            select(SegmentOverlayModel)
            .join(Segment, Segment.id == SegmentOverlayModel.segment_id)
            .join(Job, Job.id == Segment.job_id)
            .where(SegmentOverlayModel.id == placement_id, Job.user_id == current_user.id)
        )
    ).scalar_one_or_none()
    if placement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Penempatan tidak ditemukan"
        )

    changes = payload.model_dump(exclude_none=True)
    if "position" in changes and changes["position"] not in OVERLAY_POSITIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Posisi '{changes['position']}' tidak dikenal.",
        )
    if "transition" in changes and changes["transition"] not in OVERLAY_TRANSITIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Transisi '{changes['transition']}' tidak dikenal.",
        )

    for field, value in changes.items():
        setattr(placement, field, value)

    if placement.end_s <= placement.start_s:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Waktu akhir harus setelah waktu mulai.",
        )

    # Disunting pengguna berarti ia sudah meninjau usulan ini.
    placement.suggested = False

    await db.commit()
    await db.refresh(placement)
    asset = await _get_owned_asset(db, current_user.id, placement.asset_id)

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
    placement = (
        await db.execute(
            select(SegmentOverlayModel)
            .join(Segment, Segment.id == SegmentOverlayModel.segment_id)
            .join(Job, Job.id == Segment.job_id)
            .where(SegmentOverlayModel.id == placement_id, Job.user_id == current_user.id)
        )
    ).scalar_one_or_none()
    if placement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Penempatan tidak ditemukan"
        )

    await db.delete(placement)
    await db.commit()


@router.post(
    "/segments/{segment_id}/suggest",
    response_model=SuggestResponse,
    summary="Usulkan penempatan overlay dari kata kunci transkrip",
)
async def suggest_placements(
    segment_id: UUID, current_user: CurrentUserOrDev, db: DbSession
) -> SuggestResponse:
    """Cari tag aset di dalam kata-kata segmen, lalu usulkan penempatan.

    Aturan usulan:

    * Kata kunci harus cocok dengan **kata utuh** setelah tanda baca dibuang.
      Pencocokan substring polos akan membuat tag "ang" cocok dengan "orang",
      "tangan", "angan" — dan overlay muncul di tempat yang tidak berhubungan.
    * Tiap aset hanya diusulkan SEKALI per segmen; berulang kali menyebut
      "grafik" tidak berarti grafiknya perlu tampil lima kali.
    * Usulan ditaruh pada kata yang cocok, dengan durasi bawaan 3 detik.
    * Usulan yang tumpang tindih dengan usulan lain digeser, bukan ditumpuk —
      dua overlay pada piksel yang sama saling menutupi.
    """
    segment = await _get_owned_segment(db, current_user.id, segment_id)

    assets = (
        await db.execute(
            select(OverlayAssetModel).where(OverlayAssetModel.user_id == current_user.id)
        )
    ).scalars().all()
    if not assets:
        return SuggestResponse(segment_id=segment.id, created=[], matched_tags=[])

    transcript = (
        await db.execute(
            select(Transcript).where(Transcript.job_id == segment.job_id).order_by(Transcript.id).limit(1)
        )
    ).scalar_one_or_none()
    words = (transcript.words if transcript and transcript.words else []) or []

    # Kata yang jatuh di dalam rentang segmen, digeser ke waktu relatif segmen.
    local_words: list[tuple[str, float]] = []
    for entry in words:
        if not isinstance(entry, dict):
            continue
        raw_start = entry.get("start_s") or entry.get("start")
        text = str(entry.get("text") or entry.get("word") or "")
        if raw_start is None or not text:
            continue
        try:
            when = float(raw_start)
        except (TypeError, ValueError):
            continue
        if segment.start_s <= when < segment.end_s:
            local_words.append((text, when - segment.start_s))

    existing = (
        await db.execute(
            select(SegmentOverlayModel).where(SegmentOverlayModel.segment_id == segment.id)
        )
    ).scalars().all()
    occupied = [(p.start_s, p.end_s) for p in existing]
    already_used = {p.asset_id for p in existing}

    segment_duration = segment.end_s - segment.start_s
    default_span = min(3.0, max(0.5, segment_duration))

    created: list[SegmentOverlayModel] = []
    matched: list[str] = []

    for asset in assets:
        if asset.id in already_used:
            continue
        tags = _asset_tags(asset.tags)
        if not tags:
            continue

        hit_at: float | None = None
        hit_tag: str | None = None
        for text, when in local_words:
            # Buang tanda baca; bandingkan sebagai kata utuh (lihat catatan rute).
            cleaned = re.sub(r"[^\w]+", "", text, flags=re.UNICODE).lower()
            if not cleaned:
                continue
            for tag in tags:
                if cleaned == tag or (len(tag) >= 4 and cleaned.startswith(tag)):
                    hit_at = when
                    hit_tag = tag
                    break
            if hit_at is not None:
                break

        if hit_at is None:
            continue

        start = max(0.0, min(hit_at, max(0.0, segment_duration - 0.5)))
        end = min(segment_duration, start + default_span)
        # Geser bila bertabrakan, selama masih muat di dalam segmen.
        attempts = 0
        while any(not (end <= s or start >= e) for s, e in occupied) and attempts < 20:
            start += 0.5
            end = min(segment_duration, start + default_span)
            attempts += 1
        if end <= start:
            continue

        placement = SegmentOverlayModel(
            segment_id=segment.id,
            asset_id=asset.id,
            start_s=round(start, 2),
            end_s=round(end, 2),
            position=asset.default_position,
            scale=asset.default_scale,
            opacity=100,
            transition="fade",
            transition_ms=300,
            suggested=True,
        )
        db.add(placement)
        occupied.append((placement.start_s, placement.end_s))
        created.append(placement)
        matched.append(hit_tag or "")

    if created:
        await db.commit()
        for placement in created:
            await db.refresh(placement)

    asset_by_id = {a.id: a for a in assets}
    return SuggestResponse(
        segment_id=segment.id,
        created=[
            _placement_response(p, asset_by_id[p.asset_id]) for p in created
        ],
        matched_tags=sorted({t for t in matched if t}),
    )
