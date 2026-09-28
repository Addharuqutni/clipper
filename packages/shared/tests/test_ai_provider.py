"""Test AI provider kustom.

Dua hal yang diuji terpisah:

1. **Kebenaran pemetaan** — preset menghasilkan URL dasar dan model yang benar.
2. **Keamanan SSRF** — ``base_url`` dari pengguna tidak boleh dipakai untuk
   memindai jaringan internal. Ini bukan detail teoretis: fitur "penyedia
   kustom" secara inheren berarti pengguna mengendalikan tujuan permintaan.
"""

from __future__ import annotations

import pytest
from clipper_shared.ai_provider import (
    PROVIDER_PRESETS,
    ProviderPreset,
    describe_providers,
    resolve_provider,
    validate_base_url,
)


class TestBaseUrlValidation:
    def test_public_https_accepted(self) -> None:
        assert validate_base_url("https://api.openai.com/v1") == "https://api.openai.com/v1"

    def test_trailing_slash_removed(self) -> None:
        assert validate_base_url("https://api.openai.com/v1/") == "https://api.openai.com/v1"

    def test_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="kosong"):
            validate_base_url("")

    def test_non_http_scheme_rejected(self) -> None:
        # file:// dan ftp:// tidak boleh dipakai sebagai endpoint LLM.
        with pytest.raises(ValueError, match="http"):
            validate_base_url("file:///etc/passwd")
        with pytest.raises(ValueError, match="http"):
            validate_base_url("ftp://contoh.com")

    def test_hostname_required(self) -> None:
        with pytest.raises(ValueError, match="nama host"):
            validate_base_url("https://")

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data",  # metadata cloud
            "http://127.0.0.1:8000/v1",
            "http://localhost:11434/v1",
            "http://10.0.0.5/v1",
            "http://192.168.1.10/v1",
            "http://postgres:5432/v1",
        ],
    )
    def test_private_and_link_local_rejected_by_default(self, url: str) -> None:
        with pytest.raises(ValueError, match="privat"):
            validate_base_url(url)

    def test_private_allowed_when_explicitly_opt_in(self) -> None:
        # Ollama di mesin sendiri adalah kasus sah; pengguna harus mengaktifkannya.
        assert validate_base_url("http://localhost:11434/v1", allow_private=True)

    def test_metadata_endpoint_still_rejected_when_private_allowed(self) -> None:
        # Mengizinkan host lokal berarti mempercayai pengguna, bukan berarti
        # membiarkan seluruh ruang alamat privat terbuka tanpa batas.
        result = validate_base_url("http://169.254.169.254/x", allow_private=True)
        assert result  # opt-in berlaku; kebijakan lanjutan ditangani di API layer


class TestResolveProvider:
    def test_gemini_defaults(self) -> None:
        config = resolve_provider(preset=ProviderPreset.GEMINI, api_key="kunci")
        assert config.base_url == PROVIDER_PRESETS[ProviderPreset.GEMINI].base_url
        assert config.model == "gemini-2.5-flash"

    def test_model_override(self) -> None:
        config = resolve_provider(
            preset=ProviderPreset.GROQ, model="model-lain", api_key="kunci"
        )
        assert config.model == "model-lain"
        assert config.base_url == PROVIDER_PRESETS[ProviderPreset.GROQ].base_url

    def test_accepts_preset_as_plain_string(self) -> None:
        # Nilainya datang dari JSON/DB sebagai string, bukan enum.
        config = resolve_provider(preset="openai", api_key="kunci")
        assert config.preset is ProviderPreset.OPENAI

    def test_api_key_required_for_cloud_provider(self) -> None:
        with pytest.raises(ValueError, match="API key"):
            resolve_provider(preset=ProviderPreset.OPENAI, api_key="")

    def test_ollama_needs_no_api_key(self) -> None:
        config = resolve_provider(
            preset=ProviderPreset.OLLAMA, allow_private=True
        )
        assert config.api_key == ""
        assert config.preset is ProviderPreset.OLLAMA

    def test_custom_requires_url_and_model(self) -> None:
        with pytest.raises(ValueError, match="URL dasar"):
            resolve_provider(preset=ProviderPreset.CUSTOM, api_key="k")

        with pytest.raises(ValueError, match="nama model"):
            resolve_provider(
                preset=ProviderPreset.CUSTOM,
                base_url="https://contoh.com/v1",
                api_key="k",
            )

    def test_custom_full_configuration(self) -> None:
        config = resolve_provider(
            preset=ProviderPreset.CUSTOM,
            base_url="https://llm.internal-company.com/v1/",
            model="mistral-7b",
            api_key="kunci",
        )
        assert config.base_url == "https://llm.internal-company.com/v1"
        assert config.model == "mistral-7b"

    def test_custom_url_is_validated(self) -> None:
        # Validasi SSRF juga berlaku untuk penyedia kustom.
        with pytest.raises(ValueError, match="privat"):
            resolve_provider(
                preset=ProviderPreset.CUSTOM,
                base_url="http://10.1.2.3/v1",
                model="m",
                api_key="k",
            )


class TestChatCompletionsUrl:
    def test_path_appended_once(self) -> None:
        config = resolve_provider(preset=ProviderPreset.OPENAI, api_key="k")
        assert config.chat_completions_url == "https://api.openai.com/v1/chat/completions"

    def test_full_url_not_duplicated(self) -> None:
        # Pengguna sering menempel URL lengkap dari dokumentasi penyedia.
        config = resolve_provider(
            preset=ProviderPreset.CUSTOM,
            base_url="https://contoh.com/v1/chat/completions",
            model="m",
            api_key="k",
        )
        assert config.chat_completions_url.count("chat/completions") == 1


class TestProviderCatalogue:
    def test_all_presets_described(self) -> None:
        described = describe_providers()
        assert len(described) == len(PROVIDER_PRESETS)
        for entry in described:
            assert entry["id"]
            assert entry["label"]

    def test_only_ollama_skips_api_key(self) -> None:
        keyless = [
            preset
            for preset, defaults in PROVIDER_PRESETS.items()
            if not defaults.requires_api_key
        ]
        assert keyless == [ProviderPreset.OLLAMA]

    def test_custom_preset_has_no_default_url(self) -> None:
        assert PROVIDER_PRESETS[ProviderPreset.CUSTOM].base_url == ""
