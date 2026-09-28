"""Router reframe: daftar mode crop dan pratinjau geometrinya.

Mode crop menentukan bagaimana video lanskap ditempatkan di kanvas 9:16.
Endpoint di sini memberi UI daftar mode beserta penjelasannya, dan — yang lebih
berguna — **pratinjau geometri**: berapa piksel bar yang akan muncul untuk
dimensi sumber tertentu. Tanpa ini, pengguna baru tahu hasilnya setelah
menunggu render selesai.

Semua perhitungan berasal dari :mod:`clipper_shared.reframe`, yang bebas dari
OpenCV/MediaPipe sehingga API (container A) tidak perlu dependensi berat.
"""

from __future__ import annotations

from clipper_shared.reframe import (
    DEFAULT_CROP_MODE,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    CropMode,
    build_filter_chain,
    describe_mode,
    plan_letterbox,
)
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

router = APIRouter()


class CropModeInfo(BaseModel):
    """Satu mode reframing beserta penjelasan untuk pengguna."""

    id: str
    label: str
    description: str
    #: True bila mode ini memerlukan deteksi wajah (lebih mahal di CPU).
    requires_face_detection: bool
    #: True bila video sumber tampil utuh (tidak ada bagian yang dipotong).
    preserves_full_frame: bool


class CropModeListResponse(BaseModel):
    """Daftar mode yang tersedia."""

    modes: list[CropModeInfo]
    default: str


class LetterboxPreviewRequest(BaseModel):
    """Permintaan pratinjau: berapa bar yang dibutuhkan untuk dimensi ini."""

    source_width: int = Field(..., gt=0, le=16_384)
    source_height: int = Field(..., gt=0, le=16_384)
    mode: CropMode = DEFAULT_CROP_MODE


class LetterboxPreviewResponse(BaseModel):
    """Geometri hasil untuk dimensi sumber yang diberikan."""

    mode: str
    source_width: int
    source_height: int
    output_width: int
    output_height: int
    scaled_width: int
    scaled_height: int
    #: Tinggi bar atas dan bawah (0 bila mode pelacakan wajah).
    bar_height_top: int
    bar_height_bottom: int
    #: Lebar bar kiri dan kanan (0 untuk sumber yang lebih tinggi dari 9:16).
    bar_width_left: int
    bar_width_right: int
    #: Rasio bar terhadap tinggi keluaran — untuk peringatan di UI bila besar.
    bar_ratio: float
    filter_chain: str
    warning: str = ""


#: Definisi mode. ``requires_face_detection`` penting untuk memberi tahu
#: pengguna bahwa satu mode akan jauh lebih lambat di CPU tanpa GPU.
_MODE_DEFINITIONS: list[tuple[CropMode, str, bool, bool]] = [
    (CropMode.FACE_TRACK, "Face Tracking", True, False),
    (CropMode.BLACK_BARS, "Centered on Black Bars", False, True),
    (CropMode.BLURRED_FILL, "Centered on Blurred Fill", False, True),
]


@router.get(
    "/modes",
    response_model=CropModeListResponse,
    summary="Daftar mode reframing yang tersedia",
)
async def list_crop_modes() -> CropModeListResponse:
    """Kembalikan semua mode crop beserta penjelasannya.

    Tidak memerlukan autentikasi: ini metadata statis yang dipakai untuk mengisi
    dropdown, dan membiarkannya publik menyederhanakan halaman pratinjau.
    """
    modes = [
        CropModeInfo(
            id=mode.value,
            label=label,
            description=describe_mode(mode),
            requires_face_detection=requires_face,
            preserves_full_frame=preserves,
        )
        for mode, label, requires_face, preserves in _MODE_DEFINITIONS
    ]
    return CropModeListResponse(modes=modes, default=DEFAULT_CROP_MODE.value)


@router.post(
    "/preview",
    response_model=LetterboxPreviewResponse,
    summary="Pratinjau geometri crop untuk dimensi sumber",
)
async def preview_geometry(payload: LetterboxPreviewRequest) -> LetterboxPreviewResponse:
    """Hitung di mana video akan ditempatkan dan seberapa besar barnya.

    Dipakai UI untuk menampilkan peringatan seperti "video akan punya bar cukup
    besar di kiri-kanan" sebelum pengguna menunggu render.
    """
    if payload.source_width <= 0 or payload.source_height <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Dimensi sumber harus positif.",
        )

    geometry = plan_letterbox(payload.source_width, payload.source_height)

    bar_height_total = OUTPUT_HEIGHT - geometry.scaled_height
    bar_width_total = OUTPUT_WIDTH - geometry.scaled_width

    if payload.mode is CropMode.FACE_TRACK:
        bar_height_top = bar_height_bottom = 0
        bar_width_left = bar_width_right = 0
    else:
        bar_height_top = geometry.offset_y
        bar_height_bottom = bar_height_total - geometry.offset_y
        bar_width_left = geometry.offset_x
        bar_width_right = bar_width_total - geometry.offset_x

    bar_ratio = bar_height_total / OUTPUT_HEIGHT if OUTPUT_HEIGHT else 0.0

    warning = _geometry_warning(payload.mode, bar_ratio, bar_width_total)

    return LetterboxPreviewResponse(
        mode=payload.mode.value,
        source_width=payload.source_width,
        source_height=payload.source_height,
        output_width=OUTPUT_WIDTH,
        output_height=OUTPUT_HEIGHT,
        scaled_width=geometry.scaled_width,
        scaled_height=geometry.scaled_height,
        bar_height_top=bar_height_top,
        bar_height_bottom=bar_height_bottom,
        bar_width_left=bar_width_left,
        bar_width_right=bar_width_right,
        bar_ratio=round(bar_ratio, 4),
        filter_chain=build_filter_chain(payload.mode, geometry),
        warning=warning,
    )


def _geometry_warning(mode: CropMode, bar_ratio: float, bar_width_total: int) -> str:
    """Susun peringatan yang bisa ditampilkan langsung ke pengguna.

    Pengguna perlu tahu sebelum render, bukan setelah: bar besar memakan banyak
    area layar, dan video sangat lebar akan menghasilkan klip berukuran kecil.
    """
    if mode is CropMode.FACE_TRACK:
        return ""

    if bar_width_total > 0:
        return (
            "Sumber lebih tinggi daripada 9:16, sehingga akan ada bar di kiri dan "
            "kanan. Pertimbangkan mode Face Tracking agar bingkai terisi penuh."
        )

    if bar_ratio > 0.4:
        return (
            f"Bar akan menempati sekitar {bar_ratio * 100:.0f}% tinggi layar. "
            "Video tampil utuh, tetapi area aktif menjadi lebih kecil."
        )

    return ""


@router.get(
    "/modes/{mode}/preview",
    response_model=LetterboxPreviewResponse,
    summary="Pratinjau via query string (untuk pemakaian cepat)",
)
async def preview_geometry_get(
    mode: CropMode,
    source_width: int = Query(..., gt=0, le=16_384),
    source_height: int = Query(..., gt=0, le=16_384),
) -> LetterboxPreviewResponse:
    """Versi GET dari pratinjau, agar bisa dipanggil langsung dari browser."""
    return await preview_geometry(
        LetterboxPreviewRequest(
            source_width=source_width,
            source_height=source_height,
            mode=mode,
        )
    )
