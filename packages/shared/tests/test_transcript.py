"""Test unit mendalam untuk clipper_shared.transcript."""

from __future__ import annotations

import pytest
from clipper_shared.transcript import (
    TranscriptConflictError,
    WordIndexOutOfBoundsError,
    build_full_text,
    normalize_word,
    normalize_words,
    update_word_in_list,
)


def test_normalize_word_whisper_format() -> None:
    raw = {"word": "halo", "start_s": 1.25, "end_s": 1.80, "score": 0.95}
    w = normalize_word(raw)
    assert w is not None
    assert w.text == "halo"
    assert w.start_s == 1.25
    assert w.end_s == 1.80
    assert w.extra == {"score": 0.95}
    assert w.to_dict() == {"text": "halo", "start_s": 1.25, "end_s": 1.80, "score": 0.95}


def test_normalize_word_youtube_format() -> None:
    raw = {"text": "dunia", "start": 2.0, "end": 2.5}
    w = normalize_word(raw)
    assert w is not None
    assert w.text == "dunia"
    assert w.start_s == 2.0
    assert w.end_s == 2.5
    assert w.to_dict() == {"text": "dunia", "start_s": 2.0, "end_s": 2.5}


def test_normalize_word_invalid_or_empty() -> None:
    assert normalize_word({}) is None
    assert normalize_word({"text": "   ", "start_s": 0.0, "end_s": 1.0}) is None
    assert normalize_word("not a dict") is None
    assert normalize_word({"text": "valid", "start_s": "not-a-number"}) is None


def test_normalize_words_json_string_and_list() -> None:
    raw_list = [
        {"text": "satu", "start_s": 0.0, "end_s": 0.5},
        {"word": "dua", "start": 0.6, "end": 1.0},
    ]
    words = normalize_words(raw_list)
    assert len(words) == 2
    assert words[0].text == "satu"
    assert words[1].text == "dua"
    assert build_full_text(words) == "satu dua"


def test_update_word_success() -> None:
    words = normalize_words([
        {"text": "saya", "start_s": 0.0, "end_s": 0.5},
        {"text": "mkan", "start_s": 0.6, "end_s": 1.0},
        {"text": "nasi", "start_s": 1.1, "end_s": 1.5},
    ])

    updated, removed = update_word_in_list(words, index=1, new_text="makan", expected_text="mkan")
    assert removed is False
    assert len(updated) == 3
    assert updated[1].text == "makan"
    assert updated[1].start_s == 0.6
    assert build_full_text(updated) == "saya makan nasi"


def test_update_word_removal() -> None:
    words = normalize_words([
        {"text": "suara", "start_s": 0.0, "end_s": 0.5},
        {"text": "[batuk]", "start_s": 0.6, "end_s": 1.0},
        {"text": "terdengar", "start_s": 1.1, "end_s": 1.5},
    ])

    updated, removed = update_word_in_list(words, index=1, new_text="", expected_text="[batuk]")
    assert removed is True
    assert len(updated) == 2
    assert [w.text for w in updated] == ["suara", "terdengar"]
    assert build_full_text(updated) == "suara terdengar"


def test_update_word_conflict_error() -> None:
    words = normalize_words([{"text": "asli", "start_s": 0.0, "end_s": 1.0}])

    with pytest.raises(TranscriptConflictError) as exc:
        update_word_in_list(words, index=0, new_text="revisi", expected_text="beda")
    assert "sudah berubah di tempat lain" in str(exc.value)


def test_update_word_out_of_bounds() -> None:
    words = normalize_words([{"text": "tunggal", "start_s": 0.0, "end_s": 1.0}])

    with pytest.raises(WordIndexOutOfBoundsError):
        update_word_in_list(words, index=5, new_text="gagal")

    with pytest.raises(WordIndexOutOfBoundsError):
        update_word_in_list(words, index=-1, new_text="gagal")
