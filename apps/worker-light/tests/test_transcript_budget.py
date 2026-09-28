"""Transkrip yang melebihi konteks model gagal jelas, tidak dipotong diam-diam.

Sebelumnya transkrip dipotong di 120.000 karakter: video panjang hanya
menghasilkan klip dari bagian awal, tanpa error apa pun.
"""

from __future__ import annotations

import pytest


def _words(minutes: int) -> list[dict[str, object]]:
    # ~2 kata per detik, seperti percakapan normal.
    return [{"text": "katakata", "start_s": i / 2} for i in range(minutes * 120)]


def _provider(context_tokens: int | None) -> dict[str, object]:
    return {
        "preset": "custom",
        "base_url": "https://api.example.com/v1",
        "model": "m",
        "api_key": "k",
        "allow_private_host": False,
        "default_direction": "",
        "context_tokens": context_tokens,
    }


def test_transkrip_melebihi_konteks_gagal_sebelum_memanggil_penyedia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx
    from worker_light.scoring_client import score_job

    def no_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("penyedia tidak boleh dipanggil")

    monkeypatch.setattr(httpx.Client, "post", no_network)

    outcome = score_job(words=_words(60), target_count=3, provider=_provider(8_192))  # type: ignore[arg-type]

    assert outcome.segments == []
    assert outcome.error and "melebihi kapasitas konteks" in outcome.error


def test_transkrip_4_jam_tidak_dipotong() -> None:
    from worker_light.scoring_client import _render_transcript

    last_line = _render_transcript(_words(240)).splitlines()[-1]
    last_start = float(last_line[1 : last_line.index("s]")])
    assert last_start > 239 * 60
