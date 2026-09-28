"""Deteksi jendela konteks dari respons ``GET /models`` berbagai penyedia."""

from __future__ import annotations

from app.services.ai_settings import context_tokens_from_models


def test_membaca_field_konteks_yang_lazim() -> None:
    for key in ("context_length", "max_model_len", "input_token_limit"):
        payload = {"data": [{"id": "m", key: 200_000}]}
        assert context_tokens_from_models(payload, "m") == 200_000


def test_hanya_model_yang_dipilih() -> None:
    payload = {"data": [{"id": "lain", "context_length": 8_192}, {"id": "m", "context_length": 1_048_576}]}
    assert context_tokens_from_models(payload, "m") == 1_048_576


def test_id_berawalan_models_ala_gemini() -> None:
    payload = {"data": [{"id": "models/gemini-2.5-flash", "input_token_limit": 1_048_576}]}
    assert context_tokens_from_models(payload, "gemini-2.5-flash") == 1_048_576


def test_tidak_dilaporkan_atau_tidak_ada_mengembalikan_none() -> None:
    assert context_tokens_from_models({"data": [{"id": "m", "object": "model"}]}, "m") is None
    assert context_tokens_from_models({"data": [{"id": "lain", "context_length": 1}]}, "m") is None
    assert context_tokens_from_models({"error": "x"}, "m") is None
