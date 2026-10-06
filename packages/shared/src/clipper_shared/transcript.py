"""Modul transkrip: model kata bertimestamp, normalisasi format, dan mutasi teks.

Modul ini adalah antarmuka tunggal untuk operasi kata hasil STT:
1. Menormalkan varian timestamp (Whisper: start_s/end_s, YouTube: start/end) ke format kanonikal.
2. Melakukan mutasi kata atomik (update ejaan atau hapus kata kosong) dengan pemeriksaan konkurensi.
3. Menjaga sinkronisasi teks utuh (``full_text``) saat kata-kata berubah.
4. Menyediakan adapter baca/tulis transkrip di basis data SQLite untuk worker.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from clipper_shared.db import get_db_connection, utc_now


class TranscriptError(Exception):
    """Kesalahan domain operasi transkrip."""


class WordIndexOutOfBoundsError(TranscriptError, IndexError):
    """Indeks kata di luar rentang daftar kata."""


class TranscriptConflictError(TranscriptError):
    """Teks kata yang diharapkan tidak cocok dengan kata saat ini (konkurensi)."""


@dataclass(frozen=True)
class TranscriptWord:
    """Satu kata bertimestamp dalam bentuk kanonikal."""

    text: str
    start_s: float
    end_s: float
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Ubah ke format JSON serializable yang kanonikal."""
        data = {**self.extra, "text": self.text, "start_s": self.start_s, "end_s": self.end_s}
        return data


def normalize_word(raw: Any) -> TranscriptWord | None:
    """Ubah representasi mentah menjadi objek TranscriptWord kanonikal.

    Mendukung format Whisper (``start_s``/``end_s``) dan YouTube (``start``/``end``).
    Mengabaikan data tidak valid atau teks kosong.
    """
    if not isinstance(raw, dict):
        return None

    text = str(raw.get("text") or raw.get("word") or "").strip()
    if not text:
        return None

    try:
        start_s = float(raw.get("start_s") if raw.get("start_s") is not None else raw.get("start", 0.0))
        end_s = float(raw.get("end_s") if raw.get("end_s") is not None else raw.get("end", 0.0))
    except (TypeError, ValueError):
        return None

    # Simpan atribut tambahan seperti speaker ID atau confidence score
    extra = {k: v for k, v in raw.items() if k not in {"text", "word", "start_s", "end_s", "start", "end"}}
    return TranscriptWord(text=text, start_s=start_s, end_s=end_s, extra=extra)


def normalize_words(raw: Any) -> list[TranscriptWord]:
    """Ubah daftar mentah JSONB/list menjadi daftar TranscriptWord kanonikal."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []

    if not isinstance(raw, list):
        return []

    words: list[TranscriptWord] = []
    for entry in raw:
        word = normalize_word(entry)
        if word is not None:
            words.append(word)
    return words


def build_full_text(words: list[TranscriptWord]) -> str:
    """Gabungkan kata-kata menjadi teks utuh yang koheren."""
    return " ".join(w.text for w in words)


def update_word_in_list(
    words: list[TranscriptWord],
    index: int,
    new_text: str,
    *,
    expected_text: str | None = None,
) -> tuple[list[TranscriptWord], bool]:
    """Perbarui teks kata pada indeks tertentu atau hapus kata jika teks baru kosong.

    Returns:
        tuple[list[TranscriptWord], bool]: (daftar_kata_baru, is_removed)

    Raises:
        WordIndexOutOfBoundsError: Indeks di luar rentang.
        TranscriptConflictError: expected_text tidak cocok dengan kata saat ini.
    """
    if index < 0 or index >= len(words):
        raise WordIndexOutOfBoundsError(
            f"Indeks kata {index} di luar rentang (0–{max(0, len(words) - 1)})."
        )

    current_word = words[index]
    if expected_text is not None and current_word.text != expected_text.strip():
        raise TranscriptConflictError(
            "Transkrip sudah berubah di tempat lain. Muat ulang lalu coba lagi."
        )

    cleaned = new_text.strip()
    result = list(words)
    if cleaned:
        result[index] = TranscriptWord(
            text=cleaned,
            start_s=current_word.start_s,
            end_s=current_word.end_s,
            extra=current_word.extra,
        )
        return result, False

    result.pop(index)
    return result, True


# --- Adapter Persistensi SQLite untuk Worker -------------------------------


def save_transcript(
    *,
    job_id: str,
    language: str | None,
    words: list[dict[str, Any]] | list[TranscriptWord],
    full_text: str | None = None,
    model_used: str = "",
) -> None:
    """Simpan atau perbarui transkrip job dengan kata-kata yang dikanonikalisasi."""
    canonical_words = (
        words if words and isinstance(words[0], TranscriptWord) else normalize_words(words)
    )  # type: ignore[arg-type]
    raw_dicts = [w.to_dict() for w in canonical_words]  # type: ignore[union-attr]
    computed_text = full_text or build_full_text(canonical_words)  # type: ignore[arg-type]

    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM transcripts WHERE job_id = %s", (job_id,))
        cursor.execute(
            """
            INSERT INTO transcripts (id, job_id, language, words, full_text, model_used, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                job_id,
                language,
                json.dumps(raw_dicts),
                computed_text,
                model_used,
                utc_now(),
            ),
        )


def load_transcript(job_id: str) -> dict[str, Any] | None:
    """Ambil transkrip job terbaru dalam bentuk kanonikal."""
    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT language, words, full_text, model_used FROM transcripts "
            "WHERE job_id = %s ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        )
        row = cursor.fetchone()

    if row is None:
        return None

    words_raw = row[1] or []
    words = normalize_words(words_raw)
    return {
        "language": row[0],
        "words": [w.to_dict() for w in words],
        "full_text": row[2] or build_full_text(words),
        "model_used": row[3] or "",
    }
