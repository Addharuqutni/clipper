"""Paket subtitle: parser sumber + generator ASS.

Dua tanggung jawab yang dipisah dengan sengaja:

* :mod:`clipper_shared.subtitles.youtube` — mengubah subtitle YouTube
  (json3/srt/vtt) menjadi daftar kata. Dipakai untuk MELEWATI Whisper.
* :mod:`clipper_shared.subtitles.ass` — mengubah daftar kata menjadi berkas
  ASS dengan efek karaoke, siap dibakar FFmpeg.

Keduanya menghasilkan/menerima tipe yang sama (:class:`SubtitleWord`), sehingga
tahap hilir tidak perlu tahu dari mana kata itu berasal — Whisper atau subtitle
YouTube.
"""

from __future__ import annotations

from clipper_shared.subtitles.ass import (
    ANIMATION_KINDS,
    DEFAULT_CHUNK_SIZE,
    REF_HEIGHT,
    REF_WIDTH,
    SubtitleCue,
    SubtitleStyle,
    SubtitleWord,
    build_cues,
    collect_words,
    format_ass_time,
    render_ass,
)
from clipper_shared.subtitles.youtube import (
    SubtitleTrack,
    parse_json3,
    parse_srt,
    parse_subtitle,
    pick_track,
)

__all__ = [
    "ANIMATION_KINDS",
    "DEFAULT_CHUNK_SIZE",
    "REF_HEIGHT",
    "REF_WIDTH",
    "SubtitleCue",
    "SubtitleStyle",
    "SubtitleTrack",
    "SubtitleWord",
    "build_cues",
    "collect_words",
    "format_ass_time",
    "parse_json3",
    "parse_srt",
    "parse_subtitle",
    "pick_track",
    "render_ass",
]
