"""Uji koneksi penyedia AI: resolusi konfigurasi dan satu permintaan kecil.

Dipisah dari :mod:`app.services.ai_settings` karena tujuannya berbeda:
``ai_settings`` menyimpan pengaturan, sedangkan modul ini MEMBUKTIKAN bahwa
konfigurasi bekerja dan menerjemahkan kegagalan menjadi saran yang bisa
ditindaklanjuti. Kunci tersimpan boleh dipakai (lihat
:func:`resolve_probe_config`), tetapi tidak pernah dicatat ke log dan tidak
pernah disimpan ulang.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from clipper_shared.ai_provider import AIProviderConfig, ProviderPreset
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import ai_settings

#: Uji koneksi harus cepat; model besar bisa lambat, jadi batasnya lebih longgar
#: daripada panggilan biasa tetapi tetap mencegah permintaan menggantung.
TEST_TIMEOUT_S = 45.0


async def resolve_probe_config(
    db: AsyncSession,
    user_id: Any,
    *,
    preset: ProviderPreset,
    base_url: str | None,
    model: str | None,
    api_key: str,
    allow_private_host: bool,
) -> AIProviderConfig:
    """Konfigurasi untuk uji koneksi, dengan fallback ke kunci tersimpan.

    **Kunci tersimpan dipakai bila field kunci kosong.** Form pengaturan
    membiarkan field kunci kosong dengan placeholder "(tersimpan)" karena server
    tidak pernah mengembalikan nilainya. Tanpa pengambilan kunci tersimpan di
    sini, menekan "Uji koneksi" setelah menyimpan akan SELALU gagal dengan
    "memerlukan API key" — padahal kuncinya ada dan sudah tersimpan.

    Raises:
        BadRequestError: konfigurasi tidak sah (400, bukan 500).
    """
    override_fields = bool(base_url and base_url.strip()) or bool(model and model.strip())
    stored_key = ""
    if not api_key.strip() and not (override_fields or allow_private_host):
        row = await ai_settings.load_settings(db, user_id)
        # Hanya untuk preset yang sama dengan yang tersimpan: preset lain berarti
        # alamat lain, dan kunci tidak boleh dikirim ke sana.
        if row is not None and row.preset == preset.value:
            stored_key = ai_settings.decrypt_api_key(row)

    return ai_settings.resolve_configured_provider(
        preset=preset,
        base_url=base_url,
        model=model,
        api_key=api_key.strip() or stored_key,
        allow_private=allow_private_host,
    )


async def probe_provider(config: AIProviderConfig) -> dict[str, Any]:
    """Panggil endpoint chat completions dengan prompt minimal.

    Kesalahan dibedakan dengan sengaja: konfigurasi (pesan perbaikan), jaringan
    (saran periksa koneksi), dan penyedia (saran periksa key/nama model).
    """
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
            response = await client.post(config.chat_completions_url, headers=headers, json=body)
    except httpx.TimeoutException:
        return {
            "ok": False,
            "message": f"Penyedia tidak merespons dalam {TEST_TIMEOUT_S:.0f} detik.",
            "resolved_base_url": config.base_url,
            "resolved_model": config.model,
            "hint": "Periksa koneksi jaringan, atau coba model yang lebih kecil.",
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "message": f"Tidak dapat menghubungi penyedia: {exc}",
            "resolved_base_url": config.base_url,
            "resolved_model": config.model,
            "hint": "Pastikan URL dasar benar dan dapat dijangkau dari server ini.",
        }

    latency_ms = int((time.perf_counter() - started) * 1000)

    if response.status_code >= 400:
        return {
            "ok": False,
            "message": f"Penyedia menolak permintaan (HTTP {response.status_code}).",
            "resolved_base_url": config.base_url,
            "resolved_model": config.model,
            "latency_ms": latency_ms,
            "hint": hint_for_status(response.status_code),
        }

    try:
        data = response.json()
    except ValueError:
        return {
            "ok": False,
            "message": "Penyedia membalas dengan format yang bukan JSON.",
            "resolved_base_url": config.base_url,
            "resolved_model": config.model,
            "latency_ms": latency_ms,
            "hint": (
                "URL mungkin menunjuk ke halaman web, bukan endpoint API. "
                "Pastikan diakhiri dengan /v1."
            ),
        }

    sample = extract_sample(data)
    if not sample:
        return {
            "ok": False,
            "message": "Penyedia membalas tanpa isi yang dapat dibaca.",
            "resolved_base_url": config.base_url,
            "resolved_model": config.model,
            "latency_ms": latency_ms,
            "hint": "Format balasan tidak dikenali; pastikan penyedia kompatibel OpenAI.",
        }

    return {
        "ok": True,
        "message": "Koneksi berhasil.",
        "resolved_base_url": config.base_url,
        "resolved_model": config.model,
        "latency_ms": latency_ms,
        "sample": sample,
    }


def hint_for_status(code: int) -> str:
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


def extract_sample(data: object) -> str:
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
