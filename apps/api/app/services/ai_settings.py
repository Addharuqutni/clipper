"""Service penyedia AI: baca/tulis pengaturan, dekripsi kunci, deteksi konteks.

Uji koneksi tinggal di :mod:`app.services.provider_probe`. Semua logika non-HTTP
tinggal di sini. Kesalahan konfigurasi dilempar sebagai
:class:`~app.services.errors.BadRequestError`; lapisan HTTP memetakannya ke
respons 400 dengan pesan yang sama seperti sebelumnya.
"""

from __future__ import annotations

from typing import Any

import httpx
from clipper_shared.ai_provider import (
    AIProviderConfig,
    ProviderConfig,
    ProviderPreset,
    env_provider_config,
    max_video_minutes,
    provider_config_problem,
    resolve_provider,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import EncryptedBlob, TokenCipher
from app.models.ai_provider_settings import AiProviderSettings
from app.services.errors import BadRequestError

# --- Akses pengaturan -------------------------------------------------------


async def load_settings(db: AsyncSession, user_id: Any) -> AiProviderSettings | None:
    """Ambil satu baris pengaturan milik pengguna."""
    return (
        await db.execute(select(AiProviderSettings).where(AiProviderSettings.user_id == user_id))
    ).scalar_one_or_none()


async def ai_config_problem(db: AsyncSession, user_id: Any) -> str | None:
    """Alasan skoring AI akan gagal untuk pengguna ini, atau ``None`` bila siap.

    Sumbernya sama dengan worker (``storage.load_provider_config``): baris
    tersimpan bila ada, selain itu cadangan env. Dicek sebelum job dibuat supaya
    kesalahan konfigurasi muncul seketika, bukan setelah unduhan dan transkripsi
    yang bisa memakan puluhan menit.
    """
    row = await load_settings(db, user_id)
    config: ProviderConfig = (
        {
            "preset": row.preset or ProviderPreset.CUSTOM.value,
            "base_url": row.base_url or "",
            "model": row.model or "",
            "api_key": decrypt_api_key(row),
            "allow_private_host": bool(row.allow_private_host),
            "default_direction": "",
            "context_tokens": row.context_tokens,
        }
        if row is not None
        else env_provider_config()
    )
    return provider_config_problem(config)


def saved_settings_fields(row: AiProviderSettings) -> dict[str, Any]:
    """Field respons pengaturan tersimpan — TANPA pernah menyertakan kunci API."""
    return {
        "preset": row.preset,
        "base_url": row.base_url,
        "model": row.model,
        "has_api_key": bool(row.api_key_encrypted),
        "allow_private_host": row.allow_private_host,
        "default_direction": row.default_direction,
        "context_tokens": row.context_tokens,
        "max_video_minutes": max_video_minutes(row.context_tokens),
        "updated_at": row.updated_at,
    }


# --- Enkripsi kunci API -----------------------------------------------------


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


def encrypt_api_key(user_id: Any, api_key: str) -> str:
    """Enkripsi kunci API sebelum disimpan."""
    return _cipher().encrypt(api_key, aad=_key_aad(user_id)).to_b64()


def decrypt_api_key(row: AiProviderSettings | None) -> str:
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


# --- Simpan / hapus pengaturan ----------------------------------------------


def resolve_configured_provider(
    *,
    preset: ProviderPreset,
    base_url: str | None,
    model: str | None,
    api_key: str,
    allow_private: bool,
) -> AIProviderConfig:
    """Resolver yang sama seperti saat dipakai — bukan aturan terpisah."""
    try:
        return resolve_provider(
            preset=preset,
            base_url=base_url,
            model=model,
            api_key=api_key,
            allow_private=allow_private,
        )
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


async def save_settings(
    db: AsyncSession,
    user_id: Any,
    *,
    preset: ProviderPreset,
    base_url: str | None,
    model: str | None,
    api_key: str,
    clear_api_key: bool,
    allow_private_host: bool,
    default_direction: str | None,
    context_tokens: int | None,
) -> AiProviderSettings:
    """Simpan pengaturan penyedia AI pengguna.

    Konfigurasi divalidasi lebih dulu (termasuk pemeriksaan SSRF), sehingga
    pengaturan yang tidak dapat dipakai tidak pernah tersimpan dan baru ketahuan
    saat pemrosesan video berjalan.

    Raises:
        BadRequestError: konfigurasi tidak sah.
    """
    existing = await load_settings(db, user_id)
    new_key = api_key.strip()
    stored_key = "" if clear_api_key else decrypt_api_key(existing)

    resolved = resolve_configured_provider(
        preset=preset, base_url=base_url, model=model, api_key=new_key or stored_key, allow_private=allow_private_host
    )

    # Kunci tersimpan HANYA boleh dipakai ulang untuk alamat yang sama. Tanpa
    # aturan ini, mengganti base URL tanpa mengetik ulang kunci mengirim kunci
    # lama (mis. milik OpenAI) ke alamat baru saat deteksi konteks di bawah.
    same_host = existing is not None and (existing.base_url or "").rstrip("/") == resolved.base_url.rstrip("/")
    if not new_key and stored_key and not same_host:
        stored_key = ""
        try:
            resolved = resolve_provider(
                preset=preset,
                base_url=base_url,
                model=model,
                api_key="",
                allow_private=allow_private_host,
            )
        except ValueError as exc:
            raise BadRequestError(
                "Alamat penyedia berubah, jadi API key lama tidak dipakai untuk "
                "alamat baru. Isi ulang API key."
            ) from exc

    encrypted: str | None
    if new_key:
        encrypted = encrypt_api_key(user_id, new_key)
    elif stored_key and existing is not None:
        encrypted = existing.api_key_encrypted
    else:
        encrypted = None

    # Konteks: isian pengguna > deteksi dari penyedia > nilai lama (hanya bila
    # modelnya sama; konteks model lain tidak berlaku).
    detected = context_tokens or await detect_context_tokens(resolved)
    if detected is None and existing is not None and existing.model == resolved.model:
        detected = existing.context_tokens

    if existing is None:
        existing = AiProviderSettings(
            user_id=user_id,
            preset=resolved.preset.value,
            base_url=resolved.base_url,
            model=resolved.model,
            api_key_encrypted=encrypted,
            allow_private_host=allow_private_host,
            default_direction=default_direction,
            context_tokens=detected,
        )
        db.add(existing)
    else:
        existing.preset = resolved.preset.value
        existing.base_url = resolved.base_url
        existing.model = resolved.model
        existing.api_key_encrypted = encrypted
        existing.allow_private_host = allow_private_host
        existing.default_direction = default_direction
        existing.context_tokens = detected

    await db.commit()
    await db.refresh(existing)
    return existing


async def delete_settings(db: AsyncSession, user_id: Any) -> None:
    """Hapus pengaturan pengguna, termasuk kunci API tersimpan."""
    row = await load_settings(db, user_id)
    if row is not None:
        await db.delete(row)
        await db.commit()


# --- Deteksi jendela konteks ------------------------------------------------

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


async def detect_context_tokens(config: AIProviderConfig) -> int | None:
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
