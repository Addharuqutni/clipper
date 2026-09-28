"""Pengaman skoring: batas waktu video, potongan tanpa jeda, dan cadangan tanpa AI."""

from __future__ import annotations

from worker_light.scoring_client import chunk_words, heuristic_segments, normalize_segments


def _words(duration_s: float, step: float = 0.5) -> list[dict[str, object]]:
    """Bicara terus-menerus tanpa jeda sepanjang ``duration_s``."""
    count = int(duration_s / step)
    return [{"text": f"w{i}", "start_s": i * step, "end_s": (i + 1) * step} for i in range(count)]


def _raw(start: float, end: float) -> dict[str, object]:
    return {"start_s": start, "end_s": end, "score": 80, "label": "x", "reason": "y"}


def test_segmen_di_luar_durasi_video_dibuang_atau_dipotong() -> None:
    hasil = normalize_segments([_raw(-5, 40), _raw(590, 640), _raw(700, 740), _raw(100, 140)], max_end_s=600)
    assert [(s.start_s, s.end_s) for s in hasil] == [(100, 140)]
    # 590-640 dipotong ke 590-600 (10 detik) sehingga gugur di saringan durasi.


def test_bicara_tanpa_jeda_tetap_dipotong_maks_60_detik() -> None:
    chunks = chunk_words(_words(600))
    assert chunks, "video 10 menit harus menghasilkan potongan"
    assert all(30 <= end - start <= 60 for start, end in chunks), chunks


def test_cadangan_tanpa_ai_memilih_jumlah_yang_diminta_dan_terurut() -> None:
    segments = heuristic_segments(_words(600), target_count=3)
    assert len(segments) == 3
    assert [s.start_s for s in segments] == sorted(s.start_s for s in segments)
