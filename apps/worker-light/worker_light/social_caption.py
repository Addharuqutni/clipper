"""Generator caption + hashtag untuk unggahan klip (PRD FR-4.2).

Dipanggil sinkron dari API (lewat ``asyncio.to_thread``): pengguna menunggu
jawabannya di halaman klip, jadi tidak perlu antrean.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from clipper_shared.ai_provider import effective_allow_private, resolve_provider

from worker_light import storage
from worker_light.scoring_client import (
    _extract_content,
    recover_json_objects,
    strip_markdown_fences,
)

logger = logging.getLogger(__name__)

MAX_HASHTAGS = 8


class CaptionError(RuntimeError):
    """Kegagalan dengan pesan yang layak ditampilkan ke pengguna."""


def _segment_text(job_id: str, start_s: float, end_s: float) -> str:
    transcript = storage.load_transcript(job_id)
    if transcript is None:
        return ""
    return " ".join(
        str(word.get("text") or "").strip()
        for word in transcript["words"]
        if start_s <= float(word.get("start_s") or 0.0) < end_s
    ).strip()


def generate_social_caption(job_id: str, start_s: float, end_s: float, label: str = "") -> dict[str, Any]:
    """Minta LLM menulis caption + hashtag untuk satu klip.

    Returns:
        ``{"caption": str, "hashtags": list[str]}``.

    Raises:
        CaptionError: transkrip kosong, konfigurasi AI salah, atau penyedia gagal.
    """
    text = _segment_text(job_id, start_s, end_s)
    if not text:
        raise CaptionError("Transkrip klip ini kosong; caption tidak dapat dibuat.")

    provider = storage.load_provider_config(job_id)
    try:
        config = resolve_provider(
            preset=provider["preset"],
            base_url=provider["base_url"],
            model=provider["model"],
            api_key=provider["api_key"],
            allow_private=effective_allow_private(provider),
        )
    except ValueError as exc:
        raise CaptionError(f"Konfigurasi penyedia AI tidak sah: {exc}") from exc

    messages = [
        {
            "role": "system",
            "content": (
                "Anda menulis caption media sosial (TikTok, Reels, Shorts) dalam bahasa "
                "yang sama dengan transkrip. Balas HANYA JSON: "
                '{"caption": "<1-2 kalimat, memancing rasa ingin tahu, tanpa hashtag>", '
                f'"hashtags": ["<maks {MAX_HASHTAGS} hashtag relevan tanpa spasi>"]}}'
            ),
        },
        {"role": "user", "content": f"Judul klip: {label}\n\nTranskrip klip:\n{text[:6000]}"},
    ]
    headers = {"content-type": "application/json"}
    if config.api_key:
        headers["authorization"] = f"Bearer {config.api_key}"
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                config.chat_completions_url,
                headers=headers,
                json={
                    "model": config.model,
                    "messages": messages,
                    "temperature": 0.8,
                    "response_format": {"type": "json_object"},
                },
            )
    except httpx.HTTPError as exc:
        raise CaptionError(f"Tidak dapat menghubungi penyedia AI: {exc}") from exc
    if response.status_code >= 400:
        raise CaptionError(f"Penyedia AI menolak permintaan (HTTP {response.status_code}).")

    objects, _ = recover_json_objects(strip_markdown_fences(_extract_content(response.text)))
    if not objects:
        raise CaptionError("Penyedia AI membalas tanpa JSON yang dapat dibaca.")
    data = objects[0]
    hashtags = []
    for tag in data.get("hashtags") or []:
        cleaned = re.sub(r"[^\w]", "", str(tag).lstrip("#"))
        if cleaned and f"#{cleaned}" not in hashtags:
            hashtags.append(f"#{cleaned}")
    return {"caption": str(data.get("caption") or "").strip(), "hashtags": hashtags[:MAX_HASHTAGS]}
