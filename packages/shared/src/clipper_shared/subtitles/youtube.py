"""Parser subtitle YouTube untuk melewati tahap Whisper bila memungkinkan.

Pola diambil dari repo referensi `jipraks/yt-short-clipper`
(``json3_parser.py`` dan ``srt_parser.py``).

**Alasan modul ini ada:** menjalankan Whisper pada CPU memerlukan waktu
0,5-0,9x realtime (TECH_SPEC §0.1). Padahal banyak video YouTube sudah punya
subtitle — sering lengkap dengan timestamp per kata. Bila tersedia, memakai
subtitle itu memangkas tahap transkripsi dari puluhan menit menjadi hitungan
detik. Ini keuntungan besar yang tidak dimanfaatkan PRD.

**Urutan preferensi:**
1. ``json3`` ber-``wTransport`` — punya waktu per kata, kualitas terbaik.
2. ``json3`` biasa — waktu per segmen saja.
3. ``srt`` / ``vtt`` — waktu per cue, dipakai bila json3 tidak ada.

Semua format menghasilkan ``list[SubtitleWord]`` yang sama, sehingga tahap
hilir (generator ASS, skoring LLM) tidak perlu tahu asalnya.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass

from clipper_shared.subtitles.ass import SubtitleWord

#: Urutan kode bahasa yang dicoba bila pengguna tidak menentukan pilihan.
#: ``-orig`` didahulukan karena itu trek asli pembicara, bukan terjemahan.
PREFERRED_LANG_SUFFIXES = ("-orig", "")


@dataclass(frozen=True, slots=True)
class SubtitleTrack:
    """Satu trek subtitle yang tersedia untuk sebuah video."""

    lang: str
    ext: str
    url: str
    name: str = ""
    #: True bila trek ini dibuat otomatis oleh YouTube (kualitas lebih rendah).
    automatic: bool = False

    @property
    def base_lang(self) -> str:
        """``id-orig`` -> ``id``; ``pt-BR`` -> ``pt``."""
        return self.lang.lower().replace("_", "-").split("-")[0]


def pick_track(
    tracks: list[SubtitleTrack],
    preferred_lang: str | None = None,
    spoken_lang: str | None = None,
) -> SubtitleTrack | None:
    """Pilih trek terbaik: bahasa dulu, lalu manual > otomatis, lalu format.

    ``preferred_lang`` (pilihan eksplisit pengguna) bersifat WAJIB: bila tidak
    ada trek dalam bahasa itu, hasilnya ``None`` sehingga pemanggil memakai
    Whisper. Subtitle bahasa lain (mis. terjemahan Inggris dari video
    berbahasa Indonesia) bukan transkrip ucapan — teks itu akan terbakar ke
    klip dan dinilai LLM.

    Tanpa ``preferred_lang`` (deteksi otomatis), bahasa dicari berurutan:
    ``spoken_lang`` (bahasa video menurut yt-dlp), lalu bahasa trek ``-orig``
    (trek otomatis dalam bahasa asli pembicara). Tanpa langkah ini, video
    berbahasa Indonesia dengan subtitle manual Arab/Inggris/Indonesia bisa
    mendapat trek Arab hanya karena urutannya.

    Di dalam bahasa yang sama, subtitle manual diutamakan: subtitle otomatis
    YouTube sering salah mengenali kata.
    """
    if not tracks:
        return None

    def base(lang: str) -> str:
        return lang.lower().replace("_", "-").split("-")[0]

    if preferred_lang:
        candidates = [t for t in tracks if t.base_lang == base(preferred_lang)]
        if not candidates:
            return None
    else:
        orig = next((t.base_lang for t in tracks if t.lang.endswith("-orig")), None)
        candidates = tracks
        for wanted in (spoken_lang, orig):
            if not wanted:
                continue
            matching = [t for t in tracks if t.base_lang == base(wanted)]
            if matching:
                candidates = matching
                break

    # json3 lebih kaya (bisa memuat waktu per kata); srt/vtt jadi cadangan.
    def rank(track: SubtitleTrack) -> tuple[int, int, int]:
        manual_first = 0 if not track.automatic else 1
        format_rank = {"json3": 0, "vtt": 1, "srt": 2}.get(track.ext, 3)
        orig_first = 0 if track.lang.endswith("-orig") else 1
        return (manual_first, format_rank, orig_first)

    return min(candidates, key=rank)


def _clean_text(raw: str) -> str:
    """Buang tag markup dan dekode entitas HTML.

    YouTube menyisipkan ``<c>``, ``<00:00:01.234>`` dan sejenisnya ke dalam
    teks json3; kalau dibiarkan, tag itu ikut tercetak sebagai subtitle.
    """
    without_tags = re.sub(r"<[^>]+>", "", raw)
    return html.unescape(without_tags).strip()


def parse_json3(payload: str) -> list[SubtitleWord]:
    """Ubah subtitle ``json3`` menjadi daftar kata.

    **Dua skala waktu yang berbeda di dalam satu berkas.** Ini sumber bug yang
    pernah membuat SELURUH transkrip bertumpuk di 4 detik pertama:

    * ``event.tStartMs`` — waktu absolut event, diukur dari awal video.
    * ``seg.tOffsetMs`` — offset HARUS relatif terhadap ``tStartMs`` event itu,
      **bukan** waktu absolut. Menjumlahkannya salah membuat semua kata dari
      setiap event berada di sekitar 0–4 detik, dan seluruh pipeline hilir
      (segmen, subtitle, overlay) menjadi tidak sinkron.

    Bila ``wTransport`` (segmen berisi ``tOffsetMs`` + durasi) tersedia, ia
    dipakai — hasilnya jauh lebih presisi untuk efek karaoke. Bila tidak, teks
    segmen dibagi rata sepanjang durasi segmen sebagai perkiraan.

    Muatan yang tidak dapat di-parse dikembalikan sebagai daftar kosong, bukan
    exception: berkas subtitle diunduh dari jaringan dan respons yang terpotong
    adalah keadaan yang wajar. Melempar di sini akan menggagalkan seluruh job
    padahal jalur mundurnya (menjalankan Whisper) masih tersedia.
    """
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return []

    if not isinstance(data, dict):
        return []
    events = data.get("events")
    if not isinstance(events, list):
        return []

    words: list[SubtitleWord] = []
    for event in events:
        segs = event.get("segs")
        if not segs:
            continue

        # Waktu absolut awal event. Event tanpa tStartMs dilewati: tanpa acuan
        # ini, tOffsetMs tidak punya arti dan menebak akan menghasilkan kata
        # yang menempel di detik 0.
        event_start_ms = event.get("tStartMs")
        if event_start_ms is None:
            continue
        try:
            event_start_s = float(event_start_ms) / 1000.0
        except (TypeError, ValueError):
            continue

        # Durasi total event, batas atas untuk segmen terakhir yang tidak punya
        # durasi sendiri. Dipakai agar end_s tidak melewati event berikutnya.
        event_duration_ms = event.get("dDurationMs")
        event_end_s: float | None = None
        if event_duration_ms is not None:
            try:
                event_end_s = event_start_s + float(event_duration_ms) / 1000.0
            except (TypeError, ValueError):
                event_end_s = None

        for seg in segs:
            offset_ms = seg.get("tOffsetMs") or 0
            text = _clean_text(seg.get("utf8", ""))
            if not text:
                continue

            try:
                # RELATIF terhadap event — inilah perbaikannya.
                start_s = event_start_s + float(offset_ms) / 1000.0
            except (TypeError, ValueError):
                start_s = event_start_s

            duration_ms = seg.get("dDurationMs")
            if duration_ms is not None:
                try:
                    end_s = start_s + float(duration_ms) / 1000.0
                except (TypeError, ValueError):
                    end_s = start_s + max(0.2, len(text) * 0.035)
            else:
                # Estimasi: ~0,035 detik per karakter adalah laju baca rata-rata
                # subtitle otomatis. Dipakai hanya bila durasi tidak tersedia.
                end_s = start_s + max(0.2, len(text) * 0.035)

            # Jangan biarkan sebuah kata melewati akhir event-nya: itu pertanda
            # durasi yang salah dan akan membuat cue saling menimpa.
            if event_end_s is not None:
                end_s = min(end_s, event_end_s)
            if end_s <= start_s:
                end_s = start_s + 0.05

            # Satu segmen bisa memuat beberapa kata yang dipisah spasi.
            pieces = text.split()
            if len(pieces) <= 1:
                words.append(SubtitleWord(text=text, start_s=start_s, end_s=end_s))
                continue

            span = max(end_s - start_s, 0.05) / len(pieces)
            for index, piece in enumerate(pieces):
                words.append(
                    SubtitleWord(
                        text=piece,
                        start_s=start_s + index * span,
                        end_s=start_s + (index + 1) * span,
                    )
                )

    return _dedupe(words)


_SRT_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _to_seconds(hours: str, minutes: str, seconds: str, fraction: str) -> float:
    """Ubah komponen waktu SRT/VTT menjadi detik.

    Formatnya bisa milidetik (3 digit) atau centisecond (2 digit); keduanya
    dinormalkan dengan membagi menurut panjang digitnya.
    """
    scale = 1000.0 if len(fraction) == 3 else 100.0
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(fraction) / scale
    )


def parse_srt(payload: str) -> list[SubtitleWord]:
    """Ubah subtitle ``srt`` atau ``vtt`` menjadi daftar kata.

    Waktu pada kedua format ini hanya per cue, bukan per kata, sehingga durasi
    tiap kata dibagi rata di dalam cue. Efek karaoke tetap berjalan, hanya
    sorotannya kurang presisi bila pembicara berbicara cepat.
    """
    text = payload.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text)

    words: list[SubtitleWord] = []
    for block in blocks:
        lines = [line for line in block.split("\n") if line.strip()]
        match = None
        body_start = 0
        for index, line in enumerate(lines):
            match = _SRT_TIME.search(line)
            if match:
                body_start = index + 1
                break
        if not match:
            continue

        start_s = _to_seconds(*match.group(1, 2, 3, 4))
        end_s = _to_seconds(*match.group(5, 6, 7, 8))
        if end_s <= start_s:
            continue

        body = " ".join(_clean_text(line) for line in lines[body_start:]).strip()
        pieces = [p for p in body.split() if p]
        if not pieces:
            continue

        span = (end_s - start_s) / len(pieces)
        for index, piece in enumerate(pieces):
            words.append(
                SubtitleWord(
                    text=piece,
                    start_s=start_s + index * span,
                    end_s=start_s + (index + 1) * span,
                )
            )

    return _dedupe(words)


def _dedupe(words: list[SubtitleWord]) -> list[SubtitleWord]:
    """Buang kata kembar beruntun dan potong kata yang menimpa kata berikutnya.

    Subtitle otomatis YouTube sering mengulang kata yang sama antar segmen
    ketika ada jeda; bila dibiarkan, subtitle akan menampilkan kata kembar.

    Akhir kata dibatasi awal kata berikutnya: durasi per segmen json3 sering
    hanya perkiraan (``len * 0,035``), dan kata yang menimpa kata berikutnya
    membuat libass menumpuk dua baris subtitle sekaligus.
    """
    words = sorted(words, key=lambda w: w.start_s)
    for index, word in enumerate(words[:-1]):
        next_start = words[index + 1].start_s
        if word.start_s < next_start < word.end_s:
            words[index] = SubtitleWord(text=word.text, start_s=word.start_s, end_s=next_start)
    result: list[SubtitleWord] = []
    for word in words:
        if result:
            previous = result[-1]
            same_time = abs(previous.start_s - word.start_s) < 1e-6
            same_text = previous.text.lower() == word.text.lower()
            if same_time and same_text:
                continue
        result.append(word)
    return result


def parse_subtitle(payload: str, ext: str) -> list[SubtitleWord]:
    """Pilih parser berdasarkan ekstensi trek."""
    normalised = ext.lower().lstrip(".")
    if normalised == "json3":
        return parse_json3(payload)
    if normalised in {"srt", "vtt"}:
        return parse_srt(payload)
    raise ValueError(f"format subtitle tidak didukung: {ext!r} (didukung: json3, srt, vtt)")
