"""Router YouTube: validasi URL, unggah cookies, dan daftar trek subtitle.

Cookies YouTube setara dengan kredensial sesi penuh — siapa pun yang memegangnya
dapat mengakses akun pengguna. Karena itu:

* isi cookies **tidak pernah** dikembalikan dalam respons apa pun;
* isi cookies **tidak dicatat** ke log;
* yang dikembalikan hanya metadata: apakah sah, cookie mana yang ditemukan,
  dan peringatan bila kurang lengkap;
* cookies yang sah disimpan terenkripsi AES-256-GCM di
  ``clipper_shared.storage.youtube_cookies_path()`` dan dipakai worker saat
  yt-dlp diminta verifikasi.
"""

from __future__ import annotations

from clipper_shared.storage import youtube_cookies_path
from clipper_shared.subtitles import SubtitleTrack, pick_track
from clipper_shared.youtube_cookies import (
    MAX_COOKIE_FILE_BYTES,
    REQUIRED_COOKIES,
    cookie_warning,
    validate_cookie_content,
)
from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.security import TokenCipher

router = APIRouter()

#: AAD cookies YouTube. HARUS sama dengan ``worker_light.storage.COOKIES_AAD``.
COOKIES_AAD = b"clipper.source_media.youtube_cookies"

#: Bentuk URL YouTube yang diterima (PRD FR-1.1).
VALID_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "www.youtu.be",
    }
)


class UrlValidationRequest(BaseModel):
    """URL yang akan divalidasi."""

    url: str = Field(..., min_length=1, max_length=2048)


class UrlValidationResponse(BaseModel):
    """Hasil validasi URL."""

    valid: bool
    video_id: str = ""
    message: str = ""
    #: True bila URL memakai bentuk Shorts.
    is_short: bool = False


class CookieInfoResponse(BaseModel):
    """Hasil validasi cookies — TANPA isi cookie."""

    valid: bool
    found: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    message: str = ""
    warning: str = ""
    #: True bila cookies tersimpan dan akan dipakai untuk unduhan berikutnya.
    stored: bool = False


class CookieStatusResponse(BaseModel):
    """Apakah cookies YouTube tersimpan."""

    stored: bool


class SubtitleTrackInfo(BaseModel):
    """Satu trek subtitle yang tersedia."""

    lang: str
    ext: str
    name: str
    automatic: bool


class SubtitleTrackListResponse(BaseModel):
    """Trek subtitle beserta rekomendasi pilihan."""

    tracks: list[SubtitleTrackInfo]
    recommended: SubtitleTrackInfo | None = None
    #: Catatan tentang dampak memakai subtitle YouTube vs Whisper.
    note: str = ""


def _extract_video_id(url: str) -> tuple[str, bool]:
    """Ambil ID video dari URL YouTube.

    Returns:
        ``(video_id, is_short)``; ``video_id`` kosong bila tidak ditemukan.
    """
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if host not in VALID_HOSTS:
        return "", False

    if host.endswith("youtu.be"):
        candidate = parsed.path.lstrip("/").split("/")[0]
        return (candidate if _looks_like_id(candidate) else ""), False

    path = parsed.path
    if path.startswith("/shorts/"):
        candidate = path.removeprefix("/shorts/").split("/")[0]
        return (candidate if _looks_like_id(candidate) else ""), True

    query = parse_qs(parsed.query)
    candidate = (query.get("v") or [""])[0]
    return (candidate if _looks_like_id(candidate) else ""), False


def canonical_youtube_url(url: str) -> str | None:
    """Bentuk kanonik ``https://www.youtube.com/watch?v=<id>``, atau ``None`` bila tidak sah.

    Dipakai ``POST /jobs``: yang sampai ke yt-dlp hanya URL yang dibangun
    server dari ID 11 karakter, bukan teks bebas dari klien.
    """
    video_id, _ = _extract_video_id(url)
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else None


def _looks_like_id(candidate: str) -> bool:
    """ID YouTube panjangnya 11 karakter dan hanya berisi karakter URL-safe."""
    if len(candidate) != 11:
        return False
    return all(char.isalnum() or char in "-_" for char in candidate)


@router.post(
    "/validate-url",
    response_model=UrlValidationResponse,
    summary="Validasi bentuk URL YouTube",
)
async def validate_url(payload: UrlValidationRequest) -> UrlValidationResponse:
    """Periksa bentuk URL di sisi server.

    Validasi klien sudah ada di frontend, tetapi server tetap harus memeriksa:
    klien dapat dilewati, dan pesan kesalahan yang konsisten lebih baik.
    Endpoint ini hanya memeriksa BENTUK — ketersediaan dan durasi video baru
    diketahui saat ``yt-dlp`` dijalankan (TECH_SPEC §6 Sprint 1 butir 13).
    """
    video_id, is_short = _extract_video_id(payload.url)
    if not video_id:
        return UrlValidationResponse(
            valid=False,
            message=(
                "URL tidak valid. Gunakan youtube.com/watch?v=..., youtu.be/..., "
                "atau youtube.com/shorts/..."
            ),
        )
    return UrlValidationResponse(valid=True, video_id=video_id, is_short=is_short)


@router.post(
    "/cookies",
    response_model=CookieInfoResponse,
    summary="Validasi dan simpan cookies.txt (terenkripsi)",
)
async def upload_cookies(file: UploadFile = File(...)) -> CookieInfoResponse:
    """Validasi cookies.txt; bila sah, simpan terenkripsi untuk unduhan berikutnya.

    Isi berkas tidak pernah dikembalikan atau dicatat. Cookies yang tidak sah
    tidak disimpan (dan tidak menimpa cookies lama yang sah).
    """
    raw = await file.read(MAX_COOKIE_FILE_BYTES + 1)

    if len(raw) > MAX_COOKIE_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"Berkas terlalu besar (maks {MAX_COOKIE_FILE_BYTES // 1024} KB). "
                "cookies.txt biasanya hanya beberapa KB."
            ),
        )

    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Berkas bukan teks UTF-8. Ekspor ulang sebagai cookies.txt.",
        ) from exc

    outcome = validate_cookie_content(content)
    stored = False
    if outcome.ok:
        if not settings.TOKEN_ENCRYPTION_KEY:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="TOKEN_ENCRYPTION_KEY kosong; jalankan ulang start.cmd agar kunci dibuat.",
            )
        blob = TokenCipher.from_b64_key(settings.TOKEN_ENCRYPTION_KEY).encrypt(content, aad=COOKIES_AAD)
        path = youtube_cookies_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(blob.to_b64(), encoding="ascii")
        stored = True
    return CookieInfoResponse(
        valid=outcome.ok,
        found=list(outcome.found),
        missing=list(outcome.missing),
        message="" if outcome.ok else outcome.error,
        warning=cookie_warning(outcome),
        stored=stored,
    )


@router.get("/cookies", response_model=CookieStatusResponse, summary="Status cookies tersimpan")
async def cookie_status() -> CookieStatusResponse:
    """Apakah cookies YouTube tersimpan (tanpa isinya)."""
    return CookieStatusResponse(stored=youtube_cookies_path().is_file())


@router.delete("/cookies", status_code=status.HTTP_204_NO_CONTENT, summary="Hapus cookies tersimpan")
async def delete_cookies() -> None:
    """Hapus cookies YouTube tersimpan."""
    youtube_cookies_path().unlink(missing_ok=True)


@router.get(
    "/cookie-requirements",
    summary="Cookie apa saja yang diperlukan",
)
async def cookie_requirements() -> dict[str, object]:
    """Jelaskan syarat cookies kepada pengguna.

    Ditampilkan sebagai bantuan di UI: pengguna tidak akan tahu cookie mana yang
    diperlukan, dan pesan "cookies tidak valid" tanpa daftar ini tidak dapat
    ditindaklanjuti.
    """
    return {
        "required_any_of": list(REQUIRED_COOKIES),
        "explanation": (
            "Minimal satu cookie identitas harus ada. Ekspor cookies SAAT sudah "
            "login ke YouTube memakai ekstensi 'Get cookies.txt LOCALLY'."
        ),
        "max_file_bytes": MAX_COOKIE_FILE_BYTES,
    }


@router.post(
    "/pick-subtitle-track",
    response_model=SubtitleTrackListResponse,
    summary="Pilih trek subtitle terbaik dari daftar",
)
async def pick_subtitle_track(
    tracks: list[SubtitleTrackInfo],
    preferred_lang: str | None = None,
) -> SubtitleTrackListResponse:
    """Bantu memilih trek subtitle — jalur cepat yang melewati Whisper.

    Bila video sudah punya subtitle, memakainya memangkas tahap transkripsi dari
    puluhan menit (CPU) menjadi hitungan detik. Endpoint ini memilih trek terbaik
    dengan aturan: subtitle manual diutamakan daripada otomatis, lalu format
    ``json3`` (yang punya waktu per kata) diutamakan.
    """
    candidates = [
        SubtitleTrack(lang=t.lang, ext=t.ext, url="", name=t.name, automatic=t.automatic)
        for t in tracks
    ]
    chosen = pick_track(candidates, preferred_lang=preferred_lang)

    recommended = None
    if chosen is not None:
        recommended = SubtitleTrackInfo(
            lang=chosen.lang, ext=chosen.ext, name=chosen.name, automatic=chosen.automatic
        )

    note = (
        "Memakai subtitle YouTube melewati tahap Whisper sepenuhnya — jauh lebih "
        "cepat, tetapi quality timestamp per kata bergantung pada trek yang dipilih."
    )
    if recommended is None:
        note = "Tidak ada subtitle tersedia; transkripsi akan memakai Whisper."

    return SubtitleTrackListResponse(tracks=tracks, recommended=recommended, note=note)
