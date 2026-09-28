"""Uji koneksi penyedia AI kini tinggal di ``app.services.provider_probe``.

**Mengapa test ini ada.** Uji koneksi dipisah dari ``ai_settings`` supaya
penyimpanan pengaturan dan pembuktian konfigurasi dapat berkembang sendiri.
Pemisahan itu mudah rusak tanpa disadari: sebuah simbol yang tidak sengaja
tertinggal di ``ai_settings`` (atau hilang sama sekali) membuat route
mengembalikan 500. Test terakhir di berkas ini mengunci batas modulnya.

Logika yang diuji dipertahankan apa adanya: fallback kunci tersimpan, guard
``requires_api_key``, terjemahan kode HTTP, dan pembacaan cuplikan jawaban.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.services import ai_settings, provider_probe
from app.services.errors import BadRequestError
from clipper_shared.ai_provider import ProviderPreset


class _FakeResponse:
    """Respons httpx minimal untuk ``probe_provider``."""

    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeClient:
    """Pengganti ``httpx.AsyncClient`` yang tidak menyentuh jaringan."""

    def __init__(self, response: _FakeResponse | Exception) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def post(self, *args: object, **kwargs: object) -> _FakeResponse:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _patch_client(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse | Exception) -> None:
    monkeypatch.setattr(provider_probe.httpx, "AsyncClient", lambda **kw: _FakeClient(response))


def _config(**overrides: Any) -> Any:
    from clipper_shared.ai_provider import resolve_provider

    base_url = overrides.pop("base_url", "https://contoh.example.com/v1")
    kwargs = {
        "preset": overrides.pop("preset", ProviderPreset.OPENAI),
        "base_url": base_url,
        "model": overrides.pop("model", "contoh-model"),
        "api_key": overrides.pop("api_key", "kunci-uji"),
    }
    return resolve_provider(**kwargs)


def test_hint_menerjemahkan_kode_http() -> None:
    # Saran harus spesifik: pesan seragam membuat pengguna mencoba hal yang salah.
    assert "API key" in provider_probe.hint_for_status(401)
    assert "API key" in provider_probe.hint_for_status(403)
    assert "model" in provider_probe.hint_for_status(404).lower()
    assert "Kuota" in provider_probe.hint_for_status(429)
    assert "bermasalah" in provider_probe.hint_for_status(503)
    assert provider_probe.hint_for_status(418) == "Periksa kembali konfigurasi penyedia."


def test_extract_sample_membaca_bentuk_yang_lazim() -> None:
    assert (
        provider_probe.extract_sample({"choices": [{"message": {"content": " siap "}}]}) == "siap"
    )
    assert provider_probe.extract_sample({"choices": [{"text": "halo"}]}) == "halo"
    assert provider_probe.extract_sample({"output_text": "hai"}) == "hai"
    # Bentuk tak dikenal harus aman (string kosong), bukan KeyError.
    assert provider_probe.extract_sample({"choices": []}) == ""
    assert provider_probe.extract_sample("bukan dict") == ""
    assert provider_probe.extract_sample({"choices": [{"message": {"content": " "}}]}) == ""


def test_timeout_uji_koneksi_tetap_terbatas() -> None:
    # Batas waktu adalah bagian dari perilaku: tanpa ini uji koneksi bisa menggantung.
    assert provider_probe.TEST_TIMEOUT_S == 45.0


async def test_probe_ok_mengembalikan_cuplikan(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeResponse(200, {"choices": [{"message": {"content": "siap"}}]}))
    result = await provider_probe.probe_provider(_config())
    assert result["ok"] is True
    assert result["sample"] == "siap"
    assert result["resolved_model"] == "contoh-model"
    assert isinstance(result["latency_ms"], int)


async def test_probe_status_4xx_membawa_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeResponse(401, {}))
    result = await provider_probe.probe_provider(_config())
    assert result["ok"] is False
    assert "HTTP 401" in result["message"]
    assert "API key" in result["hint"]


async def test_probe_bukan_json_dan_tanpa_isi(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeResponse(200, ValueError("bukan json")))
    result = await provider_probe.probe_provider(_config())
    assert result["ok"] is False
    assert "bukan JSON" in result["message"]

    _patch_client(monkeypatch, _FakeResponse(200, {"choices": []}))
    result = await provider_probe.probe_provider(_config())
    assert result["ok"] is False
    assert "tanpa isi" in result["message"]


async def test_probe_timeout_dan_gangguan_jaringan(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    _patch_client(monkeypatch, httpx.TimeoutException("habis waktu"))
    result = await provider_probe.probe_provider(_config())
    assert result["ok"] is False
    assert "detik" in result["message"]

    _patch_client(monkeypatch, httpx.ConnectError("ditolak"))
    result = await provider_probe.probe_provider(_config())
    assert result["ok"] is False
    assert "Tidak dapat menghubungi" in result["message"]


async def test_field_kosong_memakai_kunci_tersimpan(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Row:
        preset = ProviderPreset.GEMINI.value
        api_key_encrypted = "blob"

    async def fake_load_settings(*_args: object, **_kwargs: object) -> _Row:
        return _Row()

    monkeypatch.setattr(ai_settings, "load_settings", fake_load_settings)
    monkeypatch.setattr(ai_settings, "decrypt_api_key", lambda row: "kunci-tersimpan")

    config = await provider_probe.resolve_probe_config(
        None,  # type: ignore[arg-type]
        "user",
        preset=ProviderPreset.GEMINI,
        base_url=None,
        model=None,
        api_key="",
        allow_private_host=False,
    )
    assert config.api_key == "kunci-tersimpan"
    assert config.preset is ProviderPreset.GEMINI


async def test_penimpaan_tidak_memakai_kunci_tersimpan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard ``requires_api_key`` harus tetap bekerja: penimpaan URL membuat 400."""

    class _Row:
        preset = ProviderPreset.GEMINI.value
        api_key_encrypted = "blob"

    async def fake_load_settings(*_args: object, **_kwargs: object) -> _Row:
        return _Row()

    monkeypatch.setattr(ai_settings, "load_settings", fake_load_settings)
    monkeypatch.setattr(ai_settings, "decrypt_api_key", lambda row: "kunci-lama")

    with pytest.raises(BadRequestError):
        await provider_probe.resolve_probe_config(
            None,  # type: ignore[arg-type]
            "user",
            preset=ProviderPreset.GEMINI,
            base_url="https://alamat-lain.example.com/v1",
            model=None,
            api_key="",
            allow_private_host=False,
        )


def test_batas_modul_terjaga() -> None:
    """Simbol uji koneksi TIDAK boleh tertinggal di ``ai_settings`` (tanpa alias)."""
    for name in (
        "resolve_probe_config",
        "probe_provider",
        "hint_for_status",
        "extract_sample",
        "TEST_TIMEOUT_S",
    ):
        assert not hasattr(ai_settings, name), f"{name} masih ada di ai_settings"
        assert hasattr(provider_probe, name), f"{name} hilang dari provider_probe"
    # Sebaliknya, tanggung jawab pengaturan tetap di ai_settings.
    for name in ("load_settings", "save_settings", "delete_settings", "decrypt_api_key"):
        assert hasattr(ai_settings, name)
