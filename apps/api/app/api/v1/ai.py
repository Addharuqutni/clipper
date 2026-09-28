"""Router AI provider: daftar preset dan uji koneksi.

Fitur "Custom AI Provider" memerlukan satu hal yang tidak bisa dilakukan tanpa
memanggil jaringan: **memastikan konfigurasi benar-benar bekerja.** Pengguna
yang salah menempel URL atau memakai model yang tidak ada akan menemukan
masalahnya di tengah pipeline pemrosesan video — jauh setelah ia menekan
tombol. Endpoint uji di sini memindahkan kegagalan itu ke depan, saat pengguna
masih melihat formulirnya.

**Endpoint uji menerima API key dan meneruskannya ke penyedia.** Key tidak
pernah dicatat ke log, tidak disimpan, dan tidak dikembalikan dalam respons.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx
from clipper_shared.ai_provider import (
    AIProviderConfig,
    ProviderConfig,
    ProviderPreset,
    describe_providers,
    env_provider_config,
    max_video_minutes,
    provider_config_problem,
    resolve_provider,
)
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.v1.auth import CurrentUserOrDev, DbSession
from app.core.security import EncryptedBlob, TokenCipher
from app.models.ai_provider_settings import AiProviderSettings

router = APIRouter()

#: Uji koneksi harus cepat; model besar bisa lambat, jadi batasnya lebih longgar
#: daripada panggilan biasa tetapi tetap mencegah permintaan menggantung.
TEST_TIMEOUT_S = 45.0


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
    row = await _load_settings(db, current_user.id)
    if row is None:
        return None
    return _to_saved_response(row)


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
    # Validasi dengan resolver yang sama seperti saat dipakai — bukan aturan
    # terpisah yang bisa menyimpang dari perilaku sebenarnya.
    # Kunci lama dipakai untuk validasi bila pengguna tidak mengisi ulang.
    existing = await _load_settings(db, current_user.id)
    new_key = payload.api_key.strip()
    stored_key = "" if payload.clear_api_key else _decrypt_api_key(existing)

    def _resolve(api_key: str) -> AIProviderConfig:
        try:
            return resolve_provider(
                preset=payload.preset,
                base_url=payload.base_url,
                model=payload.model,
                api_key=api_key,
                allow_private=payload.allow_private_host,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    resolved = _resolve(new_key or stored_key)

    # Kunci tersimpan HANYA boleh dipakai ulang untuk alamat yang sama. Tanpa
    # aturan ini, mengganti base URL tanpa mengetik ulang kunci mengirim kunci
    # lama (mis. milik OpenAI) ke alamat baru saat deteksi konteks di bawah.
    same_host = existing is not None and (existing.base_url or "").rstrip("/") == resolved.base_url.rstrip("/")
    if not new_key and stored_key and not same_host:
        stored_key = ""
        try:
            resolved = resolve_provider(
                preset=payload.preset,
                base_url=payload.base_url,
                model=payload.model,
                api_key="",
                allow_private=payload.allow_private_host,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Alamat penyedia berubah, jadi API key lama tidak dipakai untuk "
                    "alamat baru. Isi ulang API key."
                ),
            ) from exc

    encrypted: str | None
    if new_key:
        encrypted = _encrypt_api_key(current_user.id, new_key)
    elif stored_key and existing is not None:
        encrypted = existing.api_key_encrypted
    else:
        encrypted = None

    # Konteks: isian pengguna > deteksi dari penyedia > nilai lama (hanya bila
    # modelnya sama; konteks model lain tidak berlaku).
    context_tokens = payload.context_tokens or await _detect_context_tokens(resolved)
    if context_tokens is None and existing is not None and existing.model == resolved.model:
        context_tokens = existing.context_tokens

    if existing is None:
        existing = AiProviderSettings(
            user_id=current_user.id,
            preset=resolved.preset.value,
            base_url=resolved.base_url,
            model=resolved.model,
            api_key_encrypted=encrypted,
            allow_private_host=payload.allow_private_host,
            default_direction=payload.default_direction,
            context_tokens=context_tokens,
        )
        db.add(existing)
    else:
        existing.preset = resolved.preset.value
        existing.base_url = resolved.base_url
        existing.model = resolved.model
        existing.api_key_encrypted = encrypted
        existing.allow_private_host = payload.allow_private_host
        existing.default_direction = payload.default_direction
        existing.context_tokens = context_tokens

    await db.commit()
    await db.refresh(existing)
    return _to_saved_response(existing)


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
    row = await _load_settings(db, current_user.id)
    if row is not None:
        await db.delete(row)
        await db.commit()
    return None


# --- Pembantu penyimpanan -------------------------------------------------


async def _load_settings(db: DbSession, user_id: Any) -> AiProviderSettings | None:
    """Ambil satu baris pengaturan milik pengguna."""
    return (
        await db.execute(
            select(AiProviderSettings).where(AiProviderSettings.user_id == user_id)
        )
    ).scalar_one_or_none()


async def ai_config_problem(db: DbSession, user_id: Any) -> str | None:
    """Alasan skoring AI akan gagal untuk pengguna ini, atau ``None`` bila siap.

    Sumbernya sama dengan worker (``storage.load_provider_config``): baris
    tersimpan bila ada, selain itu cadangan env. Dicek sebelum job dibuat supaya
    kesalahan konfigurasi muncul seketika, bukan setelah unduhan dan transkripsi
    yang bisa memakan puluhan menit.
    """
    row = await _load_settings(db, user_id)
    config: ProviderConfig = (
        {
            "preset": row.preset or ProviderPreset.CUSTOM.value,
            "base_url": row.base_url or "",
            "model": row.model or "",
            "api_key": _decrypt_api_key(row),
            "allow_private_host": bool(row.allow_private_host),
            "default_direction": "",
            "context_tokens": row.context_tokens,
        }
        if row is not None
        else env_provider_config()
    )
    return provider_config_problem(config)


def _key_aad(user_id: Any) -> bytes:
    """AAD yang mengikat kunci API ke pengguna dan kolomnya.

    Mengikat ke ``user_id`` berarti ciphertext yang dipindah ke baris pengguna
    lain tidak dapat didekripsi — pertahanan terhadap kesalahan query maupun
    pemindahan data secara sengaja.
    """
    return TokenCipher.aad_for(str(user_id), "ai_provider", "api_key_encrypted")


def _cipher() -> TokenCipher:
    from app.core.config import settings

    return TokenCipher.from_b64_key(settings.TOKEN_ENCRYPTION_KEY)


def _encrypt_api_key(user_id: Any, api_key: str) -> str:
    """Enkripsi kunci API sebelum disimpan."""
    return _cipher().encrypt(api_key, aad=_key_aad(user_id)).to_b64()


def _decrypt_api_key(row: AiProviderSettings | None) -> str:
    """Dekripsi kunci API tersimpan; kosong bila gagal.

    Kegagalan dekripsi (kunci enkripsi berganti, data rusak) tidak boleh
    membuat seluruh halaman pengaturan error. Pengguna diberi tahu lewat
    ``has_api_key`` yang tetap true, dan akan diminta mengisi ulang saat uji
    koneksi gagal.
    """
    if row is None or not row.api_key_encrypted:
        return ""
    try:
        blob = EncryptedBlob.from_b64(row.api_key_encrypted)
        return _cipher().decrypt(blob, aad=_key_aad(row.user_id))
    except Exception:  # noqa: BLE001
        return ""


def _to_saved_response(row: AiProviderSettings) -> SavedSettingsResponse:
    """Ubah baris menjadi respons TANPA pernah menyertakan kunci API."""
    return SavedSettingsResponse(
        preset=row.preset,
        base_url=row.base_url,
        model=row.model,
        has_api_key=bool(row.api_key_encrypted),
        allow_private_host=row.allow_private_host,
        default_direction=row.default_direction,
        context_tokens=row.context_tokens,
        max_video_minutes=max_video_minutes(row.context_tokens),
        updated_at=row.updated_at,
    )


#: Nama field jendela konteks di ``GET /models`` pada berbagai penyedia:
#: OpenRouter/LiteLLM/proxy (``context_length``), vLLM (``max_model_len``),
#: dan varian lain yang lazim.
_CONTEXT_KEYS = (
    "context_length",
    "context_window",
    "max_context_length",
    "max_model_len",
    "input_token_limit",
    "inputTokenLimit",
)


def context_tokens_from_models(payload: object, model: str) -> int | None:
    """Ambil jendela konteks ``model`` dari respons ``GET /models``, bila dilaporkan."""
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and item.get("id") in (model, f"models/{model}"):
            for key in _CONTEXT_KEYS:
                value = item.get(key)
                if isinstance(value, int) and value > 0:
                    return value
            return None
    return None


async def _detect_context_tokens(config: AIProviderConfig) -> int | None:
    """Tanya penyedia berapa konteks modelnya; ``None`` bila tidak dilaporkan.

    Kegagalan tidak boleh menggagalkan penyimpanan pengaturan: banyak penyedia
    (OpenAI, Ollama) memang tidak mencantumkan konteks, dan cadangannya aman.
    URL-nya berasal dari ``resolve_provider``, jadi sudah lolos validasi SSRF.
    """
    models_url = config.chat_completions_url.removesuffix("/chat/completions") + "/models"
    headers = {"authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(models_url, headers=headers)
        response.raise_for_status()
        return context_tokens_from_models(response.json(), config.model)
    except (httpx.HTTPError, ValueError):
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

    **Kunci tersimpan dipakai bila field kunci kosong.** Form pengaturan
    membiarkan field kunci kosong dengan placeholder "(tersimpan)" karena server
    tidak pernah mengembalikan nilainya. Tanpa pengambilan kunci tersimpan di
    sini, menekan "Uji koneksi" setelah menyimpan akan SELALU gagal dengan
    "memerlukan API key" — padahal kuncinya ada dan sudah tersimpan. Pengguna
    akan menyimpulkan kuncinya rusak dan menggantinya berkali-kali.

    Kesalahan dibedakan dengan sengaja:

    * **konfigurasi** (URL tidak sah, key kurang) → pesan yang menyatakan apa
      yang harus diperbaiki;
    * **jaringan** (timeout, DNS gagal) → saran periksa koneksi;
    * **penyedia** (401, 404 model, 5xx) → saran periksa key/nama model.

    Membeda-bedakan ini penting: pesan "gagal" yang seragam membuat pengguna
    mencoba hal yang salah berulang kali.
    """
    # Fallback ke kunci tersimpan SEKARANG tidak lagi menutupi dua kesalahan
    # nyata, sehingga guard `requires_api_key` di resolve_provider tetap bekerja
    # dan menjawab 400 (bukan 401 dari penyedia):
    #   1. pengguna membiarkan field kosong padahal BELUM pernah menyimpan kunci;
    #   2. pengguna mengirim penimpaan base_url/model sehingga kunci tersimpan
    #      tidak lagi berlaku untuk konfigurasi itu.
    override_fields = bool(payload.base_url and payload.base_url.strip()) or bool(
        payload.model and payload.model.strip()
    )
    stored_key = ""
    if not payload.api_key.strip() and not (override_fields or payload.allow_private_host):
        row = await _load_settings(db, current_user.id)
        # Hanya untuk preset yang sama dengan yang tersimpan: preset lain berarti
        # alamat lain, dan kunci tidak boleh dikirim ke sana.
        if row is not None and row.preset == payload.preset.value:
            stored_key = _decrypt_api_key(row)

    try:
        config = resolve_provider(
            preset=payload.preset,
            base_url=payload.base_url,
            model=payload.model,
            api_key=payload.api_key.strip() or stored_key,
            allow_private=payload.allow_private_host,
        )
    except ValueError as exc:
        # Kesalahan konfigurasi: 400, bukan 500 — ini salah pengguna, bukan
        # kegagalan server, dan UI harus menampilkannya di dekat field terkait.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return await _probe_provider(config)


async def _probe_provider(config: AIProviderConfig) -> ProviderTestResponse:
    """Panggil endpoint chat completions dengan prompt minimal."""
    headers = {"content-type": "application/json"}
    if config.api_key:
        headers["authorization"] = f"Bearer {config.api_key}"

    body = {
        "model": config.model,
        "messages": [{"role": "user", "content": "Balas dengan satu kata: siap"}],
        # Rendah agar jawabannya dapat diprediksi dan murah.
        "temperature": 0.0,
        "max_tokens": 16,
    }

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=TEST_TIMEOUT_S) as client:
            response = await client.post(
                config.chat_completions_url, headers=headers, json=body
            )
    except httpx.TimeoutException:
        return ProviderTestResponse(
            ok=False,
            message=f"Penyedia tidak merespons dalam {TEST_TIMEOUT_S:.0f} detik.",
            resolved_base_url=config.base_url,
            resolved_model=config.model,
            hint="Periksa koneksi jaringan, atau coba model yang lebih kecil.",
        )
    except httpx.HTTPError as exc:
        return ProviderTestResponse(
            ok=False,
            message=f"Tidak dapat menghubungi penyedia: {exc}",
            resolved_base_url=config.base_url,
            resolved_model=config.model,
            hint="Pastikan URL dasar benar dan dapat dijangkau dari server ini.",
        )

    latency_ms = int((time.perf_counter() - started) * 1000)

    if response.status_code >= 400:
        return ProviderTestResponse(
            ok=False,
            message=f"Penyedia menolak permintaan (HTTP {response.status_code}).",
            resolved_base_url=config.base_url,
            resolved_model=config.model,
            latency_ms=latency_ms,
            hint=_hint_for_status(response.status_code),
        )

    try:
        data = response.json()
    except ValueError:
        return ProviderTestResponse(
            ok=False,
            message="Penyedia membalas dengan format yang bukan JSON.",
            resolved_base_url=config.base_url,
            resolved_model=config.model,
            latency_ms=latency_ms,
            hint=(
                "URL mungkin menunjuk ke halaman web, bukan endpoint API. "
                "Pastikan diakhiri dengan /v1."
            ),
        )

    sample = _extract_sample(data)
    if not sample:
        return ProviderTestResponse(
            ok=False,
            message="Penyedia membalas tanpa isi yang dapat dibaca.",
            resolved_base_url=config.base_url,
            resolved_model=config.model,
            latency_ms=latency_ms,
            hint="Format balasan tidak dikenali; pastikan penyedia kompatibel OpenAI.",
        )

    return ProviderTestResponse(
        ok=True,
        message="Koneksi berhasil.",
        resolved_base_url=config.base_url,
        resolved_model=config.model,
        latency_ms=latency_ms,
        sample=sample,
    )


def _hint_for_status(code: int) -> str:
    """Terjemahkan kode HTTP menjadi saran yang bisa ditindaklanjuti."""
    if code in {401, 403}:
        return "API key ditolak. Periksa kembali key-nya dan pastikan masih aktif."
    if code == 404:
        return "Endpoint atau nama model tidak ditemukan. Periksa URL dasar dan nama model."
    if code == 429:
        return "Kuota penyedia habis atau terlalu banyak permintaan. Coba lagi nanti."
    if code >= 500:
        return "Penyedia sedang bermasalah. Coba lagi, atau pilih penyedia lain."
    return "Periksa kembali konfigurasi penyedia."


def _extract_sample(data: object) -> str:
    """Ambil cuplikan teks dari respons bergaya OpenAI, dengan aman.

    Struktur respons berbeda antar penyedia; fungsi ini menelusuri bentuk yang
    umum dan mengembalikan string kosong bila tidak menemukan apa pun — daripada
    melempar ``KeyError`` yang akan muncul sebagai 500 tanpa penjelasan.
    """
    if not isinstance(data, dict):
        return ""

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()[:200]
            text = first.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()[:200]

    # Sebagian penyedia memakai bentuk berbeda.
    for key in ("output_text", "content", "response"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]

    return ""
