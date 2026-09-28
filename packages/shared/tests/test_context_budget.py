"""Batas durasi video mengikuti konteks model AI, dibatasi batas atas env."""

from __future__ import annotations

import pytest
from clipper_shared.ai_provider import (
    DEFAULT_CONTEXT_TOKENS,
    RESERVED_PROMPT_TOKENS,
    TRANSCRIPT_TOKENS_PER_MINUTE,
    max_video_minutes,
    provider_config_problem,
    transcript_char_budget,
)


@pytest.fixture(autouse=True)
def _ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_VIDEO_DURATION_MIN", "180")


def test_model_besar_dibatasi_batas_atas_env() -> None:
    assert max_video_minutes(1_048_576) == 180


def test_batas_atas_env_bisa_dinaikkan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_VIDEO_DURATION_MIN", "600")
    assert max_video_minutes(1_048_576) == 600


def test_model_kecil_membatasi_durasi() -> None:
    expected = (32_768 - RESERVED_PROMPT_TOKENS) // TRANSCRIPT_TOKENS_PER_MINUTE
    assert max_video_minutes(32_768) == expected
    assert 0 < expected < 180


def test_konteks_tidak_diketahui_memakai_cadangan() -> None:
    assert max_video_minutes(None) == max_video_minutes(DEFAULT_CONTEXT_TOKENS)
    assert transcript_char_budget(None) == transcript_char_budget(DEFAULT_CONTEXT_TOKENS)


def test_durasi_maksimum_muat_di_anggaran_karakter() -> None:
    """Video sepanjang batasnya harus lolos cek karakter di tahap analyze.

    Transkrip terpadat yang terukur ~970 karakter/menit; bila anggaran karakter
    lebih kecil dari itu, video yang lolos ingest akan gagal setelah transkripsi.
    """
    for context in (8_192, 32_768, 128_000, 1_048_576):
        assert transcript_char_budget(context) >= max_video_minutes(context) * 970


def test_konteks_terlalu_kecil_ditolak_sebelum_job_dibuat() -> None:
    config = {
        "preset": "custom",
        "base_url": "https://api.example.com/v1",
        "model": "tiny",
        "api_key": "k",
        "allow_private_host": False,
        "default_direction": "",
        "context_tokens": 4_096,
    }
    problem = provider_config_problem(config)  # type: ignore[arg-type]
    assert problem is not None and "terlalu kecil" in problem

    config["context_tokens"] = 128_000
    assert provider_config_problem(config) is None  # type: ignore[arg-type]
