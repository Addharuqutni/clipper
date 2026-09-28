"""Router caption: preset gaya subtitle + gaya per job.

**Mengapa preset, bukan sekadar kolom gaya di job.** Pengguna yang membuat
konten secara rutin memakai gaya yang sama berulang kali. Menyimpannya sebagai
preset berarti gaya itu disetel sekali lalu dipakai ke banyak video, dan
konsistensi visual antar-klip terjaga — yang penting untuk pengenalan kanal.

Dua tingkat yang sengaja dipisah:

* **Preset** (``subtitle_presets``) — koleksi gaya bernama milik pengguna.
* **Override per job** (``jobs.subtitle_style``) — penyimpangan sesaat untuk
  satu video, tanpa mengubah preset.

Render memilih: override job → preset bawaan pengguna → bawaan modul.

Validasi bentuk gaya dilakukan dengan **membangun ``SubtitleStyle`` sungguhan**
dari payload, bukan memeriksa kunci satu per satu. Cara itu menjamin apa pun
yang diterima API pasti dapat dirender worker — sumber kebenaran rentang dan
jenis nilai hanya ada satu, yaitu dataclass-nya.
"""

from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any
from uuid import UUID

from clipper_shared.subtitles import ANIMATION_KINDS, SubtitleStyle
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.v1.auth import CurrentUserOrDev, DbSession, get_owned_job
from app.models.subtitle_preset import SubtitlePreset
from app.models.user import User

router = APIRouter()

#: Batas jumlah preset per pengguna. Bukan batas teknis: daftar yang sangat
#: panjang membuat pemilih gaya di UI tidak lagi dapat dipindai dengan mata.
MAX_PRESETS_PER_USER = 50


class SubtitleStylePayload(BaseModel):
    """Gaya subtitle dari klien. Semua field opsional; yang absen memakai bawaan.

    Sengaja TIDAK memakai ``extra="forbid"``: klien versi lama mungkin mengirim
    kunci yang sudah tidak ada, dan menolaknya akan mematahkan alur kerja yang
    sebenarnya baik-baik saja. Kunci asing disaring sebelum validasi.
    """

    font_name: str | None = Field(default=None, max_length=120)
    font_size: int | None = Field(default=None, ge=8, le=400)
    primary_rgb: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    highlight_rgb: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    outline_rgb: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    back_rgb: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    back_alpha: int | None = Field(default=None, ge=0, le=100)
    outline: int | None = Field(default=None, ge=0, le=40)
    shadow: int | None = Field(default=None, ge=0, le=40)
    margin_side: int | None = Field(default=None, ge=0, le=1080)
    margin_vertical: int | None = Field(default=None, ge=0, le=1920)
    border_style: int | None = Field(default=None, description="1 = outline, 3 = kotak latar")
    alignment: int | None = Field(default=None, ge=1, le=9)
    bold: bool | None = None
    italic: bool | None = None
    letter_spacing: float | None = Field(default=None, ge=-10, le=40)
    animation: str | None = Field(default=None, description="none|fade|bounce|pop|karaoke")
    animation_ms: int | None = Field(default=None, ge=0, le=2000)
    animation_scale: float | None = Field(default=None, ge=1.0, le=3.0)
    chunk_size: int | None = Field(default=None, ge=1, le=12)


class PresetCreateRequest(BaseModel):
    """Payload pembuatan preset baru."""

    name: str = Field(..., min_length=1, max_length=120)
    style: SubtitleStylePayload = Field(default_factory=SubtitleStylePayload)


class PresetUpdateRequest(BaseModel):
    """Payload perubahan preset. Field yang tidak dikirim tidak diubah."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    style: SubtitleStylePayload | None = None


class PresetResponse(BaseModel):
    """Satu preset gaya subtitle."""

    id: UUID
    name: str
    style: dict[str, Any]
    #: True bila preset ini yang dipakai saat job tidak punya override.
    is_default: bool


class PresetListResponse(BaseModel):
    """Daftar preset milik pengguna."""

    items: list[PresetResponse]
    total: int


class SetDefaultRequest(BaseModel):
    """Penetapan preset bawaan; ``preset_id`` null berarti kembali ke bawaan modul."""

    preset_id: UUID | None = None


class StyleOptionsResponse(BaseModel):
    """Pilihan yang tersedia untuk UI. Dikirim agar UI tidak menebak daftarnya."""

    animations: list[str]
    defaults: dict[str, Any]
    font_size_min: int
    font_size_max: int
    alignment_min: int
    alignment_max: int
    border_styles: list[int]
    max_presets: int


class JobStyleRequest(BaseModel):
    """Override gaya untuk satu job. Kirim ``style`` null untuk menghapus override."""

    style: SubtitleStylePayload | None = None


class JobStyleResponse(BaseModel):
    """Gaya efektif sebuah job beserta asalnya."""

    job_id: UUID
    style: dict[str, Any]
    #: ``job`` | ``preset`` | ``default`` — dari mana gaya ini berasal.
    source: str
    preset_name: str | None = None


def _style_dict(payload: SubtitleStylePayload) -> dict[str, Any]:
    """Ubah payload menjadi dict gaya yang sudah tervalidasi.

    Validasi dilakukan dengan MEMBANGUN ``SubtitleStyle`` sungguhan: nilai yang
    gagal (mis. ``animation`` tidak dikenal) ditolak di sini dengan pesan yang
    menyebut field penyebabnya, dan nilai yang lolos dijamin dapat dirender.
    """
    provided = payload.model_dump(exclude_none=True)
    if not provided:
        return asdict(SubtitleStyle())

    try:
        built = SubtitleStyle(**provided)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Gaya subtitle tidak sah: {exc}",
        ) from exc

    # Kembalikan dict PENUH (termasuk bawaan) supaya gaya yang tersimpan bersifat
    # mandiri: mengubah bawaan modul di masa depan tidak akan diam-diam mengubah
    # tampilan preset yang sudah dibuat pengguna.
    return asdict(built)


def _merged_style(existing: dict[str, Any] | None, payload: SubtitleStylePayload) -> dict[str, Any]:
    """Gabungkan perubahan ke atas gaya yang sudah ada.

    PATCH yang hanya mengirim ``font_size`` tidak boleh mengembalikan warna dan
    animasi ke bawaan — itu akan menghapus pekerjaan pengguna tanpa ia memintanya.
    """
    base = dict(existing) if existing else asdict(SubtitleStyle())
    base.update(payload.model_dump(exclude_none=True))
    known = {f.name for f in fields(SubtitleStyle)}
    clean = {k: v for k, v in base.items() if k in known}
    try:
        return asdict(SubtitleStyle(**clean))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Gaya subtitle tidak sah: {exc}",
        ) from exc



async def _load_user(db: DbSession, user_id: Any) -> User | None:
    """Ambil baris ``users`` dari basis data.

    **Mengapa tidak memakai ``current_user`` langsung.** Saat ``AUTH_DISABLED=true``
    dependency autentikasi mengembalikan objek tiruan ``_DevUser`` yang hidup di
    memori, bukan baris basis data. Membaca ``default_subtitle_preset_id`` dari
    objek itu selalu menghasilkan ``None`` walau nilainya sudah tersimpan —
    gejalanya: menetapkan preset bawaan tampak berhasil (HTTP 200) tetapi tidak
    pernah berlaku. Mengambil barisnya dari DB membuat perilakunya sama di mode
    dev maupun produksi.
    """
    return (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()


@router.get(
    "/presets/options",
    response_model=StyleOptionsResponse,
    summary="Pilihan gaya yang tersedia untuk UI",
)
async def list_style_options() -> StyleOptionsResponse:
    """Daftar jenis animasi, nilai bawaan, dan rentang yang sah.

    Dikirim dari server agar UI tidak menyalin daftar animasi secara manual —
    salinan manual pasti menyimpang saat opsi baru ditambahkan di backend.
    """
    return StyleOptionsResponse(
        animations=sorted(ANIMATION_KINDS),
        defaults=asdict(SubtitleStyle()),
        font_size_min=8,
        font_size_max=400,
        alignment_min=1,
        alignment_max=9,
        border_styles=[1, 3],
        max_presets=MAX_PRESETS_PER_USER,
    )


@router.get(
    "/presets",
    response_model=PresetListResponse,
    summary="Daftar preset gaya subtitle milik pengguna",
)
async def list_presets(current_user: CurrentUserOrDev, db: DbSession) -> PresetListResponse:
    """Kembalikan semua preset milik pengguna, terlama dulu."""
    rows = (
        await db.execute(
            select(SubtitlePreset)
            .where(SubtitlePreset.user_id == current_user.id)
            .order_by(SubtitlePreset.created_at.asc())
        )
    ).scalars().all()

    # Dibaca dari DB, bukan dari ``current_user`` — lihat catatan di ``_load_user``.
    user_row = await _load_user(db, current_user.id)
    default_id = user_row.default_subtitle_preset_id if user_row else None

    return PresetListResponse(
        items=[
            PresetResponse(
                id=row.id,
                name=row.name,
                style=row.style or {},
                is_default=(row.id == default_id),
            )
            for row in rows
        ],
        total=len(rows),
    )


@router.post(
    "/presets",
    response_model=PresetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Buat preset gaya subtitle",
)
async def create_preset(
    payload: PresetCreateRequest,
    current_user: CurrentUserOrDev,
    db: DbSession,
) -> PresetResponse:
    """Buat preset baru.

    Raises:
        HTTPException: 409 bila nama sudah dipakai atau kuota preset penuh;
            422 bila gaya tidak sah.
    """
    existing = (
        await db.execute(
            select(SubtitlePreset).where(
                SubtitlePreset.user_id == current_user.id,
                SubtitlePreset.name == payload.name,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Preset bernama '{payload.name}' sudah ada.",
        )

    count = len(
        (
            await db.execute(
                select(SubtitlePreset.id).where(SubtitlePreset.user_id == current_user.id)
            )
        ).scalars().all()
    )
    if count >= MAX_PRESETS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Jumlah preset sudah mencapai batas {MAX_PRESETS_PER_USER}.",
        )

    preset = SubtitlePreset(
        user_id=current_user.id,
        name=payload.name,
        style=_style_dict(payload.style),
    )
    db.add(preset)
    await db.commit()
    await db.refresh(preset)

    user_row = await _load_user(db, current_user.id)
    return PresetResponse(
        id=preset.id,
        name=preset.name,
        style=preset.style or {},
        is_default=bool(
            user_row and user_row.default_subtitle_preset_id == preset.id
        ),
    )


@router.patch(
    "/presets/{preset_id}",
    response_model=PresetResponse,
    summary="Ubah nama dan/atau gaya preset",
)
async def update_preset(
    preset_id: UUID,
    payload: PresetUpdateRequest,
    current_user: CurrentUserOrDev,
    db: DbSession,
) -> PresetResponse:
    """Perbarui preset. Field yang tidak dikirim tidak diubah."""
    preset = (
        await db.execute(
            select(SubtitlePreset).where(
                SubtitlePreset.id == preset_id,
                SubtitlePreset.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if preset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preset tidak ditemukan")

    if payload.name is not None and payload.name != preset.name:
        clash = (
            await db.execute(
                select(SubtitlePreset).where(
                    SubtitlePreset.user_id == current_user.id,
                    SubtitlePreset.name == payload.name,
                    SubtitlePreset.id != preset.id,
                )
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Preset bernama '{payload.name}' sudah ada.",
            )
        preset.name = payload.name

    if payload.style is not None:
        preset.style = _merged_style(preset.style, payload.style)

    await db.commit()
    await db.refresh(preset)

    user_row = await _load_user(db, current_user.id)
    return PresetResponse(
        id=preset.id,
        name=preset.name,
        style=preset.style or {},
        is_default=bool(user_row and user_row.default_subtitle_preset_id == preset.id),
    )


@router.delete(
    "/presets/{preset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Hapus preset",
)
async def delete_preset(
    preset_id: UUID,
    current_user: CurrentUserOrDev,
    db: DbSession,
) -> None:
    """Hapus preset, sekaligus melepasnya dari pengguna bila ia sedang jadi bawaan."""
    preset = (
        await db.execute(
            select(SubtitlePreset).where(
                SubtitlePreset.id == preset_id,
                SubtitlePreset.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if preset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preset tidak ditemukan")

    # Lepas penunjuk bawaan LEBIH DULU. Foreign key memakai ON DELETE SET NULL,
    # tetapi mengandalkan itu saja berarti bergantung pada urutan flush ORM —
    # dan bila preset dihapus lewat jalur lain (mis. CASCADE dari user), kolom
    # ini bisa tertinggal menunjuk preset yang sudah hilang.
    user = await _load_user(db, current_user.id)
    if user is not None and user.default_subtitle_preset_id == preset.id:
        user.default_subtitle_preset_id = None

    await db.delete(preset)
    await db.commit()


@router.post(
    "/presets/default",
    response_model=PresetListResponse,
    summary="Tetapkan preset bawaan pengguna",
)
async def set_default_preset(
    payload: SetDefaultRequest,
    current_user: CurrentUserOrDev,
    db: DbSession,
) -> PresetListResponse:
    """Tetapkan preset yang dipakai bila job tidak punya override.

    ``preset_id`` bernilai null berarti kembali ke gaya bawaan modul.
    """
    user = await _load_user(db, current_user.id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pengguna tidak ditemukan")

    if payload.preset_id is not None:
        preset = (
            await db.execute(
                select(SubtitlePreset).where(
                    SubtitlePreset.id == payload.preset_id,
                    SubtitlePreset.user_id == current_user.id,
                )
            )
        ).scalar_one_or_none()
        if preset is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Preset tidak ditemukan"
            )

    user.default_subtitle_preset_id = payload.preset_id
    await db.commit()

    return await list_presets(current_user, db)


@router.get(
    "/jobs/{job_id}/style",
    response_model=JobStyleResponse,
    summary="Gaya subtitle efektif sebuah job",
)
async def get_job_style(
    job_id: UUID,
    current_user: CurrentUserOrDev,
    db: DbSession,
) -> JobStyleResponse:
    """Kembalikan gaya yang akan dipakai render, beserta dari mana asalnya.

    ``source`` penting bagi UI: pengguna perlu tahu apakah yang terlihat adalah
    gaya khusus video ini, preset bawaannya, atau bawaan sistem.
    """
    job = await get_owned_job(db, current_user.id, job_id)

    if job.subtitle_style:
        return JobStyleResponse(job_id=job.id, style=job.subtitle_style, source="job")

    # Baca dari DB (bukan current_user) agar preset bawaan ikut berlaku di mode
    # dev tanpa autentikasi — lihat catatan di ``_load_user``.
    user_row = await _load_user(db, current_user.id)
    preset_id = user_row.default_subtitle_preset_id if user_row else None

    if preset_id is not None:
        preset = (
            await db.execute(select(SubtitlePreset).where(SubtitlePreset.id == preset_id))
        ).scalar_one_or_none()
        if preset is not None and preset.style:
            return JobStyleResponse(
                job_id=job.id,
                style=preset.style,
                source="preset",
                preset_name=preset.name,
            )

    return JobStyleResponse(job_id=job.id, style=asdict(SubtitleStyle()), source="default")


@router.put(
    "/jobs/{job_id}/style",
    response_model=JobStyleResponse,
    summary="Tetapkan override gaya untuk satu job",
)
async def set_job_style(
    job_id: UUID,
    payload: JobStyleRequest,
    current_user: CurrentUserOrDev,
    db: DbSession,
) -> JobStyleResponse:
    """Tetapkan atau hapus override gaya sebuah job.

    Mengirim ``style: null`` menghapus override, sehingga job kembali mengikuti
    preset bawaan pengguna. Ini penting: tanpa jalan kembali, satu kesalahan
    penyetelan akan menempel pada job itu selamanya.
    """
    job = await get_owned_job(db, current_user.id, job_id)

    if payload.style is None:
        job.subtitle_style = None
    else:
        job.subtitle_style = _style_dict(payload.style)

    await db.commit()
    await db.refresh(job)

    if job.subtitle_style:
        return JobStyleResponse(job_id=job.id, style=job.subtitle_style, source="job")
    return await get_job_style(job_id, current_user, db)
