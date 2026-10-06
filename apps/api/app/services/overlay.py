"""Service overlay: pustaka aset B-roll/efek suara dan penempatannya pada segmen.

Logika non-HTTP: pencarian aset/segmen milik pengguna, validasi rentang, dan
algoritme usulan otomatis berbasis kata kunci.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.overlay import OVERLAY_POSITIONS, OVERLAY_TRANSITIONS
from app.models.overlay import OverlayAsset as OverlayAssetModel
from app.models.overlay import SegmentOverlay as SegmentOverlayModel
from app.models.segment import Segment
from app.models.transcript import Transcript
from app.services.errors import NotFoundError, UnprocessableError

#: Jenis konten yang diterima, dipetakan ke ``kind``.
CONTENT_KIND = (
    ("image/", "image"),
    ("video/", "video"),
    ("audio/", "audio"),
)

#: Batas ukuran satu aset overlay. B-roll adalah klip pendek/grafik; berkas
#: sebesar film panjang menandakan pengguna salah memilih berkas.
MAX_OVERLAY_BYTES = 50 * 1024 * 1024


def asset_tags(raw: str | None) -> list[str]:
    """Ubah teks tag dipisah koma menjadi daftar bersih."""
    if not raw:
        return []
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


def kind_for(content_type: str) -> str | None:
    """Tentukan ``kind`` dari content type."""
    lowered = (content_type or "").lower()
    for prefix, kind in CONTENT_KIND:
        if lowered.startswith(prefix):
            return kind
    return None


# --- Kepemilikan ------------------------------------------------------------


async def get_owned_segment(db: AsyncSession, user_id: Any, segment_id: UUID) -> Segment:
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
        raise NotFoundError("Segmen tidak ditemukan")
    return row


async def get_owned_asset(db: AsyncSession, user_id: Any, asset_id: UUID) -> OverlayAssetModel:
    """Ambil aset overlay milik user atau 404."""
    row = (
        await db.execute(
            select(OverlayAssetModel).where(
                OverlayAssetModel.id == asset_id, OverlayAssetModel.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Aset tidak ditemukan")
    return row


async def _get_owned_placement(db: AsyncSession, user_id: Any, placement_id: UUID) -> SegmentOverlayModel:
    """Ambil penempatan milik user (lewat segmen → job) atau 404."""
    placement = (
        await db.execute(
            select(SegmentOverlayModel)
            .join(Segment, Segment.id == SegmentOverlayModel.segment_id)
            .join(Job, Job.id == Segment.job_id)
            .where(SegmentOverlayModel.id == placement_id, Job.user_id == user_id)
        )
    ).scalar_one_or_none()
    if placement is None:
        raise NotFoundError("Penempatan tidak ditemukan")
    return placement


# --- DTO --------------------------------------------------------------------


def asset_fields(asset: OverlayAssetModel) -> dict[str, Any]:
    """Field respons satu aset overlay."""
    return {
        "id": asset.id,
        "kind": asset.kind,
        "name": asset.name,
        "tags": asset_tags(asset.tags),
        "size_bytes": asset.size_bytes,
        "duration_s": asset.duration_s,
        "default_position": asset.default_position,
        "default_scale": asset.default_scale,
    }


def placement_fields(placement: SegmentOverlayModel, asset: OverlayAssetModel) -> dict[str, Any]:
    """Field respons satu penempatan overlay."""
    return {
        "id": placement.id,
        "segment_id": placement.segment_id,
        "asset_id": placement.asset_id,
        "asset_name": asset.name,
        "asset_kind": asset.kind,
        "start_s": placement.start_s,
        "end_s": placement.end_s,
        "position": placement.position,
        "scale": placement.scale,
        "opacity": placement.opacity,
        "transition": placement.transition,
        "transition_ms": placement.transition_ms,
        "suggested": placement.suggested,
    }


# --- Pustaka aset -----------------------------------------------------------


async def list_assets(db: AsyncSession, user_id: Any) -> list[OverlayAssetModel]:
    """Pustaka aset pengguna, terbaru dulu."""
    return list(
        (
            await db.execute(
                select(OverlayAssetModel)
                .where(OverlayAssetModel.user_id == user_id)
                .order_by(OverlayAssetModel.created_at.desc())
            )
        ).scalars().all()
    )


def validate_upload(*, content_type: str, name: str, default_position: str, default_scale: float) -> str:
    """Validasi metadata unggahan dan kembalikan ``kind`` yang terdeteksi.

    Raises:
        UnprocessableError: jenis berkas/ukuran/posisi tidak sah.
    """
    if default_position not in OVERLAY_POSITIONS:
        raise UnprocessableError(
            f"Posisi '{default_position}' tidak dikenal; pilih salah satu {list(OVERLAY_POSITIONS)}"
        )
    if not 0 < default_scale <= 1:
        raise UnprocessableError("default_scale harus lebih dari 0 dan paling besar 1.")

    kind = kind_for(content_type)
    if kind is None:
        raise UnprocessableError(
            f"Jenis berkas '{content_type or 'tidak dikenal'}' tidak didukung. "
            "Gunakan gambar (image/*), video (video/*), atau audio (audio/*)."
        )
    return kind


def validate_content_size(content: bytes) -> None:
    """Tolak berkas kosong/terlalu besar.

    Raises:
        UnprocessableError: berkas kosong atau melebihi ``MAX_OVERLAY_BYTES``.
    """
    if not content:
        raise UnprocessableError("Berkas kosong.")
    if len(content) > MAX_OVERLAY_BYTES:
        raise UnprocessableError(
            f"Berkas {len(content) // 1024 // 1024} MB melebihi batas "
            f"{MAX_OVERLAY_BYTES // 1024 // 1024} MB."
        )


async def create_asset(
    db: AsyncSession,
    user_id: Any,
    *,
    kind: str,
    name: str,
    filename: str,
    content_type: str | None,
    tags: str,
    default_position: str,
    default_scale: float,
    content: bytes,
) -> OverlayAssetModel:
    """Simpan satu aset overlay dan kembalikan barisnya."""
    asset = OverlayAssetModel(
        user_id=user_id,
        kind=kind,
        name=name.strip()[:200] or (filename or "overlay")[:200],
        tags=",".join(asset_tags(tags)) or None,
        r2_key=await _store_asset(user_id, filename or f"overlay.{kind}", content),
        content_type=content_type,
        size_bytes=len(content),
        default_position=default_position,
        default_scale=default_scale,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return asset


async def _store_asset(user_id: Any, filename: str, content: bytes) -> str:
    """Tulis isi berkas ke penyimpanan (lewat thread; I/O berkas)."""
    from clipper_shared.storage import store_overlay_bytes

    r2_key, _ = await asyncio.to_thread(store_overlay_bytes, str(user_id), filename, content)
    return r2_key


async def delete_asset(db: AsyncSession, user_id: Any, asset_id: UUID) -> None:
    """Hapus aset beserta SEMUA penempatannya (CASCADE di basis data)."""
    from clipper_shared import storage as layout
    from clipper_shared.storage import delete_object

    asset = await get_owned_asset(db, user_id, asset_id)
    r2_key = asset.r2_key
    await db.delete(asset)
    await db.commit()
    await asyncio.to_thread(delete_object, layout.OVERLAYS, r2_key)


# --- Penempatan -------------------------------------------------------------


async def list_placements(db: AsyncSession, user_id: Any, segment_id: UUID) -> list[tuple[SegmentOverlayModel, OverlayAssetModel]]:
    """Penempatan overlay segmen, terurut waktu mulai."""
    segment = await get_owned_segment(db, user_id, segment_id)
    rows = (
        await db.execute(
            select(SegmentOverlayModel, OverlayAssetModel)
            .join(OverlayAssetModel, OverlayAssetModel.id == SegmentOverlayModel.asset_id)
            .where(SegmentOverlayModel.segment_id == segment.id)
            .order_by(SegmentOverlayModel.start_s.asc())
        )
    ).all()
    return [(p, a) for p, a in rows]


def _validate_time_range(start_s: float, end_s: float, segment_duration: float) -> None:
    """Rentang harus berada di dalam segmen dan berakhir setelah mulai."""
    if end_s > segment_duration + 0.01:
        raise UnprocessableError(
            f"Overlay berakhir pada {end_s:.1f}s, sedangkan segmen "
            f"hanya {segment_duration:.1f}s. Waktu dihitung dari awal segmen."
        )
    if end_s <= start_s:
        raise UnprocessableError("Waktu akhir harus setelah waktu mulai.")


async def create_placement(
    db: AsyncSession,
    user_id: Any,
    *,
    segment_id: UUID,
    asset_id: UUID,
    start_s: float,
    end_s: float,
    position: str | None,
    scale: float | None,
    opacity: int,
    transition: str,
    transition_ms: int,
) -> tuple[SegmentOverlayModel, OverlayAssetModel]:
    """Tempatkan aset pada rentang waktu di dalam segmen.

    Raises:
        UnprocessableError: rentang waktu/posisi/transisi tidak sah.
    """
    segment = await get_owned_segment(db, user_id, segment_id)
    asset = await get_owned_asset(db, user_id, asset_id)

    _validate_time_range(start_s, end_s, segment.end_s - segment.start_s)
    if position is not None and position not in OVERLAY_POSITIONS:
        raise UnprocessableError(f"Posisi '{position}' tidak dikenal.")
    if transition not in OVERLAY_TRANSITIONS:
        raise UnprocessableError(f"Transisi '{transition}' tidak dikenal.")

    placement = SegmentOverlayModel(
        segment_id=segment.id,
        asset_id=asset.id,
        start_s=start_s,
        end_s=end_s,
        # Bawaan diambil dari ASET, bukan konstanta: pengguna menyetel preferensi
        # posisi saat mengunggah, dan mengabaikannya akan memaksa ia mengaturnya
        # ulang di setiap penempatan.
        position=position or asset.default_position,
        scale=scale if scale is not None else asset.default_scale,
        opacity=opacity,
        transition=transition,
        transition_ms=transition_ms,
        suggested=False,
    )
    db.add(placement)
    await db.commit()
    await db.refresh(placement)
    return placement, asset


async def update_placement(
    db: AsyncSession,
    user_id: Any,
    placement_id: UUID,
    changes: dict[str, Any],
) -> tuple[SegmentOverlayModel, OverlayAssetModel]:
    """Perbarui penempatan; menyunting usulan menandainya sebagai milik pengguna.

    Raises:
        UnprocessableError: posisi/transisi tidak dikenal atau rentang tidak sah.
    """
    placement = await _get_owned_placement(db, user_id, placement_id)

    if "position" in changes and changes["position"] not in OVERLAY_POSITIONS:
        raise UnprocessableError(f"Posisi '{changes['position']}' tidak dikenal.")
    if "transition" in changes and changes["transition"] not in OVERLAY_TRANSITIONS:
        raise UnprocessableError(f"Transisi '{changes['transition']}' tidak dikenal.")

    for field, value in changes.items():
        setattr(placement, field, value)

    if placement.end_s <= placement.start_s:
        raise UnprocessableError("Waktu akhir harus setelah waktu mulai.")

    # Disunting pengguna berarti ia sudah meninjau usulan ini.
    placement.suggested = False

    await db.commit()
    await db.refresh(placement)
    asset = await get_owned_asset(db, user_id, placement.asset_id)
    return placement, asset


async def delete_placement(db: AsyncSession, user_id: Any, placement_id: UUID) -> None:
    """Hapus satu penempatan. Asetnya tetap ada di pustaka."""
    placement = await _get_owned_placement(db, user_id, placement_id)
    await db.delete(placement)
    await db.commit()


# --- Usulan otomatis --------------------------------------------------------


def suggest_for_segment(
    *,
    segment: Segment,
    assets: list[OverlayAssetModel],
    words: list[Any],
    existing: list[SegmentOverlayModel],
) -> tuple[list[SegmentOverlayModel], list[str]]:
    """Hitung usulan penempatan TANPA menyentuh basis data.

    Mengembalikan ``(usulan, tag_yang_cocok)`` — tag dilaporkan agar UI dapat
    menjelaskan MENGAPA usulan ini muncul.

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

    occupied = [(p.start_s, p.end_s) for p in existing]
    already_used = {p.asset_id for p in existing}
    segment_duration = segment.end_s - segment.start_s
    default_span = min(3.0, max(0.5, segment_duration))

    created: list[SegmentOverlayModel] = []
    matched: list[str] = []

    for asset in assets:
        if asset.id in already_used:
            continue
        tags = asset_tags(asset.tags)
        if not tags:
            continue

        hit_at: float | None = None
        hit_tag: str | None = None
        for text, when in local_words:
            # Buang tanda baca; bandingkan sebagai kata utuh (lihat docstring).
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
        occupied.append((placement.start_s, placement.end_s))
        created.append(placement)
        matched.append(hit_tag or "")

    return created, sorted({tag for tag in matched if tag})


async def suggest_placements(
    db: AsyncSession, user_id: Any, segment_id: UUID
) -> tuple[Segment, list[SegmentOverlayModel], list[OverlayAssetModel], list[str]]:
    """Cari tag aset di dalam kata-kata segmen, simpan usulan, kembalikan hasilnya.

    Returns:
        ``(segment, usulan, aset, tag_yang_cocok)``.
    """
    segment = await get_owned_segment(db, user_id, segment_id)

    assets = list(
        (
            await db.execute(select(OverlayAssetModel).where(OverlayAssetModel.user_id == user_id))
        ).scalars().all()
    )
    if not assets:
        return segment, [], [], []

    transcript = (
        await db.execute(
            select(Transcript).where(Transcript.job_id == segment.job_id).order_by(Transcript.id).limit(1)
        )
    ).scalar_one_or_none()
    words = (transcript.words if transcript and transcript.words else []) or []

    existing = list(
        (
            await db.execute(select(SegmentOverlayModel).where(SegmentOverlayModel.segment_id == segment.id))
        ).scalars().all()
    )

    created, matched = suggest_for_segment(segment=segment, assets=assets, words=list(words), existing=existing)
    for placement in created:
        db.add(placement)

    if created:
        await db.commit()
        for placement in created:
            await db.refresh(placement)

    return segment, created, assets, matched
