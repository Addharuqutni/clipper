"""Test generator ASS karaoke.

Fokus pada jebakan yang SENYAP — salahnya tidak memunculkan error, hanya
subtitle yang terlihat salah:

* warna ASS memakai urutan BGR, bukan RGB;
* waktu ASS memakai centisecond (2 digit), bukan milidetik;
* ``PlayResX/Y`` wajib dari resolusi nyata, bukan nilai tetap 1080x1920.
"""

from __future__ import annotations

import pytest
from clipper_shared.subtitles.ass import (
    SubtitleStyle,
    SubtitleWord,
    build_cues,
    format_ass_time,
    render_ass,
)


def _words(count: int) -> list[SubtitleWord]:
    return [
        SubtitleWord(text=f"kata{index}", start_s=index * 0.4, end_s=(index + 1) * 0.4)
        for index in range(count)
    ]


class TestAssTimeFormat:
    """ASS memakai centisecond. Menulis milidetik membuat waktu salah dibaca."""

    def test_centisecond_not_millisecond(self) -> None:
        # 1,23 detik harus menjadi .23 (centisecond), bukan .230 (milidetik).
        assert format_ass_time(1.23) == "0:00:01.23"

    def test_zero(self) -> None:
        assert format_ass_time(0) == "0:00:00.00"

    def test_hours_minutes_seconds(self) -> None:
        assert format_ass_time(3725.5) == "1:02:05.50"

    def test_negative_clamped(self) -> None:
        assert format_ass_time(-5) == "0:00:00.00"

    def test_rounding_does_not_overflow(self) -> None:
        # 59,999 detik membulatkan centisecond ke 100; harus jadi 1:00.00.
        assert format_ass_time(59.999) == "0:01:00.00"


class TestAssColourOrder:
    """Warna ASS adalah &HAABBGGRR. Salah urutan menukar merah dan biru."""

    def test_yellow_is_bgr_order(self) -> None:
        words = _words(2)
        ass = render_ass(words, width=1080, height=1920)
        # #FFFF00 -> BGR "00FFFF" -> &H0000FFFF&
        assert "&H0000FFFF&" in ass

    def test_custom_highlight_colour(self) -> None:
        # Merah murni #FF0000 harus jadi &H000000FF& (biru=00, hijau=00, merah=FF).
        style = SubtitleStyle(highlight_rgb="#FF0000")
        words = _words(2)
        ass = render_ass(words, width=1080, height=1920, style=style)
        assert "&H000000FF&" in ass


class TestAssPlayRes:
    """PlayResX/Y wajib resolusi nyata; nilai tetap membuat subtitle gepeng."""

    def test_portrait_uses_real_resolution(self) -> None:
        ass = render_ass(_words(2), width=1080, height=1920)
        assert "PlayResX: 1080" in ass
        assert "PlayResY: 1920" in ass

    def test_landscape_uses_real_resolution(self) -> None:
        ass = render_ass(_words(2), width=1920, height=1080)
        assert "PlayResX: 1920" in ass
        assert "PlayResY: 1080" in ass

    def test_style_scales_with_non_portrait_frame(self) -> None:
        # Font 65 pada tinggi 1920; pada tinggi 1080 harus menyusut proporsional.
        portrait = render_ass(_words(2), width=1080, height=1920)
        landscape = render_ass(_words(2), width=1920, height=1080)
        portrait_size = int(portrait.split("Style: Default,Arial Black,")[1].split(",")[0])
        landscape_size = int(landscape.split("Style: Default,Arial Black,")[1].split(",")[0])
        assert portrait_size == 65
        assert landscape_size < portrait_size


class TestCueBuilding:
    def test_one_cue_per_word(self) -> None:
        assert len(build_cues(_words(8))) == 8

    def test_words_grouped_in_chunks_of_four(self) -> None:
        cues = build_cues(_words(4), chunk_size=4)
        # Setiap cue memuat potongan 4 kata yang sama, dengan kata berbeda disorot.
        assert all("kata0" in cue.text and "kata3" in cue.text for cue in cues)

    def test_no_gaps_between_consecutive_cues(self) -> None:
        # Celah waktu membuat subtitle berkedip hilang di antara kata.
        cues = build_cues(_words(6))
        for earlier, later in zip(cues, cues[1:], strict=False):
            assert later.start_s >= earlier.end_s - 1e-9

    def test_empty_input(self) -> None:
        assert build_cues([]) == []

    def test_words_without_duration_dropped(self) -> None:
        degenerate = [SubtitleWord(text="x", start_s=1.0, end_s=1.0)]
        assert build_cues(degenerate) == []

    def test_braces_escaped(self) -> None:
        # Kurung kurawal adalah sintaks override ASS; teks pengguna tidak boleh
        # bisa menyuntikkan override-nya sendiri.
        hostile = [SubtitleWord(text="{\\fs200}besar", start_s=0.0, end_s=0.5)]
        ass = render_ass(hostile, width=1080, height=1920)
        assert "{\\fs200}" not in ass
        assert "(\\fs200)besar" in ass

    def test_invalid_chunk_size_rejected(self) -> None:
        with pytest.raises(ValueError, match="chunk_size"):
            build_cues(_words(4), chunk_size=0)


class TestAssStructure:
    def test_required_sections_present(self) -> None:
        ass = render_ass(_words(2), width=1080, height=1920)
        for section in ("[Script Info]", "[V4+ Styles]", "[Events]"):
            assert section in ass

    def test_scaled_border_and_shadow_enabled(self) -> None:
        # Tanpa ini, outline tidak ikut menskalakan bersama resolusi dan
        # terlihat terlalu tipis pada klip beresolusi tinggi.
        ass = render_ass(_words(2), width=1080, height=1920)
        assert "ScaledBorderAndShadow: yes" in ass
