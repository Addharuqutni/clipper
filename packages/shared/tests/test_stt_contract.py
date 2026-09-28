"""Test kontrak Transcriber (TECH_SPEC §5.1)."""

from __future__ import annotations

import pytest
from clipper_shared.stt import (
    FasterWhisperLocal,
    RemoteWhisperAPI,
    Transcriber,
    get_transcriber,
)


class TestTranscriberSelection:
    """Pemilihan backend lewat STT_BACKEND (TECH_SPEC §5.1)."""

    def test_local_mengembalikan_faster_whisper(self) -> None:
        transcriber = get_transcriber("local")
        assert isinstance(transcriber, FasterWhisperLocal)

    def test_remote_mengembalikan_remote_api(self) -> None:
        transcriber = get_transcriber("remote", api_key="test-key")
        assert isinstance(transcriber, RemoteWhisperAPI)

    def test_backend_case_insensitive(self) -> None:
        assert isinstance(get_transcriber("LOCAL"), FasterWhisperLocal)

    def test_backend_tidak_dikenal_ditolak(self) -> None:
        with pytest.raises(ValueError, match="tidak dikenal"):
            get_transcriber("magic")

    def test_remote_tanpa_api_key_ditolak(self) -> None:
        """Gagal cepat saat konfigurasi salah, bukan saat task berjalan."""
        with pytest.raises(ValueError, match="WHISPER_API_KEY"):
            get_transcriber("remote")

    def test_keduanya_memenuhi_protocol(self) -> None:
        """Kedua implementasi harus memenuhi Protocol agar bisa dipertukarkan."""
        assert isinstance(FasterWhisperLocal(), Transcriber)
        assert isinstance(RemoteWhisperAPI(api_key="k"), Transcriber)

    def test_default_model_adalah_small_int8(self) -> None:
        """TECH_SPEC §0.1: default MVP = small int8."""
        transcriber = get_transcriber("local")
        assert isinstance(transcriber, FasterWhisperLocal)
        assert transcriber.model_size == "small"
        assert transcriber.compute_type == "int8"
