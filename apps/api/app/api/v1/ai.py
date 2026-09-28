"""Router AI provider: daftar preset dan uji koneksi.

Fitur "Custom AI Provider" memerlukan satu hal yang tidak bisa dilakukan tanpa
memanggil jaringan: **memastikan konfigurasi benar-benar bekerja.** Pengguna
yang salah menempel URL atau memakai model yang tidak ada akan menemukan
masalahnya di tengah pipeline pemrosesan video — jauh setelah ia menekan
tombol. Endpoint uji di sini memindahkan kegagalan itu ke depan, saat pengguna
masih melihat formulirnya.

**Endpoint uji menerima API key dan meneruskannya ke penyedia.** Key tidak
pernah dicatat ke log, tidak disimpan, dan tidak dikembalikan dalam respons.

Logika non-HTTP — akses basis data, dekripsi kunci, dan deteksi konteks —
tinggal di :mod:`app.services.ai_settings`; pemanggilan pengujian koneksi
tinggal di :mod:`app.services.provider_probe`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from clipper_shared.ai_provider import ProviderPreset, describe_providers
from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from app.api.v1.auth import CurrentUserOrDev, DbSession
from app.services import ai_settings, provider_probe

router = APIRouter()


class ProviderPresetInfo(BaseModel):
    """Satu preset penyedia untuk mengisi dropdown."""

    id: str
    label: str
    default_model: str
    base_url: str
    requires_api_key: bool


class ProviderListResponse(BaseModel):
    """Daftar preset yang tersedia."""

    presets: list[ProviderPresetInfo]


class ProviderTestResponse(BaseModel):
    """Hasil uji koneksi."""

    ok: bool
    message: str
    resolved_base_url: str = ""
    resolved_model: str = ""
    latency_ms: int | None = None
    #: Cuplikan jawaban model, dipotong pendek — cukup untuk membuktikan hidup.
    sample: str = ""
    #: Kesalahan yang bisa ditindaklanjuti pengguna.
    hint: str = ""


class ProviderConfigRequest(BaseModel):
    """Konfigurasi penyedia yang akan diuji."""

    preset: ProviderPreset = ProviderPreset.GEMINI
    base_url: str | None = Field(
        default=None,
        description="Kosongkan untuk memakai URL bawaan preset.",
        max_length=2048,
    )
    model: str | None = Field(default=None, max_length=256)
    api_key: str = Field(default="", max_length=4096)
    allow_private_host: bool = Field(
        default=False,
        description="Wajib True untuk Ollama atau model di jaringan sendiri.",
    )


class SavedSettingsResponse(BaseModel):
    """Pengaturan tersimpan.

    ``api_key`` SENGAJA tidak ada di sini. Yang dikembalikan hanya penanda
    ``has_api_key``: UI perlu tahu apakah kunci sudah diisi, tetapi nilainya
    tidak boleh pernah meninggalkan server — kunci API adalah kredensial penuh.
    """

    preset: str
    base_url: str | None
    model: str | None
    has_api_key: bool
    allow_private_host: bool
    default_direction: str | None
    #: Jendela konteks model (token); ``None`` = tidak diketahui (dipakai cadangan).
    context_tokens: int | None
    #: Durasi video maksimum yang transkripnya muat di konteks model,
    #: sudah dibatasi ``MAX_VIDEO_DURATION_MIN``.
    max_video_minutes: int
    updated_at: datetime | None


class SaveSettingsRequest(BaseModel):
    """Permintaan menyimpan pengaturan penyedia."""

    preset: ProviderPreset = ProviderPreset.GEMINI
    base_url: str | None = Field(default=None, max_length=2048)
    model: str | None = Field(default=None, max_length=256)
    #: Kosongkan untuk MEMPERTAHANKAN kunci yang sudah tersimpan. Menghapus
    #: kunci memerlukan ``clear_api_key=True`` secara eksplisit, supaya
    #: menekan Simpan tanpa mengisi ulang field tidak menghapus kredensial.
    api_key: str = Field(default="", max_length=4096)
    clear_api_key: bool = False
    allow_private_host: bool = False
    default_direction: str | None = Field(default=None, max_length=2000)
    #: Isi hanya bila penyedia tidak melaporkan konteks modelnya. Kosong = deteksi
    #: otomatis dari ``GET /models``.
    context_tokens: int | None = Field(default=None, ge=1024, le=100_000_000)


@router.get(
    "/providers",
    response_model=ProviderListResponse,
    summary="Daftar preset penyedia AI",
)
async def list_providers() -> ProviderListResponse:
    """Kembalikan preset penyedia untuk mengisi formulir di UI."""
    return ProviderListResponse(
        presets=[ProviderPresetInfo(**entry) for entry in describe_providers()]
    )


@router.get(
    "/settings",
    response_model=SavedSettingsResponse | None,
    summary="Ambil pengaturan penyedia AI yang tersimpan",
)
async def get_settings(
    current_user: CurrentUserOrDev,
    db: DbSession,
) -> SavedSettingsResponse | None:
    """Kembalikan pengaturan pengguna, atau ``null`` bila belum pernah disimpan.

    ``null`` bukan kesalahan: pengguna baru memang belum punya pengaturan, dan
    UI harus menampilkan nilai bawaan dalam keadaan itu.
    """
    row = await ai_settings.load_settings(db, current_user.id)
    if row is None:
        return None
    return SavedSettingsResponse(**ai_settings.saved_settings_fields(row))


@router.put(
    "/settings",
    response_model=SavedSettingsResponse,
    summary="Simpan pengaturan penyedia AI",
)
async def save_settings(
    payload: SaveSettingsRequest,
    current_user: CurrentUserOrDev,
    db: DbSession,
) -> SavedSettingsResponse:
    """Simpan pengaturan penyedia AI pengguna.

    Konfigurasi divalidasi lebih dulu (termasuk pemeriksaan SSRF), sehingga
    pengaturan yang tidak dapat dipakai tidak pernah tersimpan dan baru ketahuan
    saat pemrosesan video berjalan.

    Raises:
        HTTPException: 400 bila konfigurasi tidak sah.
    """
    row = await ai_settings.save_settings(
        db,
        current_user.id,
        preset=payload.preset,
        base_url=payload.base_url,
        model=payload.model,
        api_key=payload.api_key,
        clear_api_key=payload.clear_api_key,
        allow_private_host=payload.allow_private_host,
        default_direction=payload.default_direction,
        context_tokens=payload.context_tokens,
    )
    return SavedSettingsResponse(**ai_settings.saved_settings_fields(row))


@router.delete(
    "/settings",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Hapus pengaturan penyedia AI",
)
async def delete_settings(
    current_user: CurrentUserOrDev,
    db: DbSession,
) -> None:
    """Hapus pengaturan pengguna, termasuk kunci API tersimpan.

    Berguna saat pengguna ingin mencabut kredensialnya dari layanan ini.
    """
    await ai_settings.delete_settings(db, current_user.id)
    return None


@router.post(
    "/test",
    response_model=ProviderTestResponse,
    summary="Uji koneksi ke penyedia AI",
)
async def test_provider(
    payload: ProviderConfigRequest,
    current_user: CurrentUserOrDev,
    db: DbSession,
) -> ProviderTestResponse:
    """Kirim satu permintaan kecil untuk memastikan konfigurasi bekerja.

    Kunci tersimpan dipakai bila field kunci kosong (lihat
    :func:`app.services.provider_probe.resolve_probe_config`), dengan dua pengecualian
    penting yang menjaga guard ``requires_api_key`` tetap bekerja dan menjawab
    400 (bukan 401 dari penyedia):

    1. pengguna membiarkan field kosong padahal BELUM pernah menyimpan kunci;
    2. pengguna mengirim penimpaan base_url/model sehingga kunci tersimpan
       tidak lagi berlaku untuk konfigurasi itu.

    Kesalahan dibedakan dengan sengaja — konfigurasi vs jaringan vs penyedia —
    karena pesan "gagal" yang seragam membuat pengguna mencoba hal yang salah
    berulang kali.

    Raises:
        HTTPException: 400 bila konfigurasi tidak sah.
    """
    config = await provider_probe.resolve_probe_config(
        db,
        current_user.id,
        preset=payload.preset,
        base_url=payload.base_url,
        model=payload.model,
        api_key=payload.api_key,
        allow_private_host=payload.allow_private_host,
    )
    result: dict[str, Any] = await provider_probe.probe_provider(config)
    return ProviderTestResponse(**result)
