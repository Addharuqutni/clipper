"""Test parser subtitle YouTube (json3, srt, vtt).

**Mengapa berkas ini ada.** Sebelum ini tidak ada satu pun test untuk parser
subtitle, dan bug yang lolos membuat SELURUH transkrip bertumpuk di 4 detik
pertama: ``seg.tOffsetMs`` diperlakukan sebagai waktu absolut, padahal ia
relatif terhadap ``event.tStartMs``. Videonya 213 detik, tetapi transkripnya
berakhir di detik 3,9 — dan tidak ada pesan kesalahan di mana pun.

Test di sini mengunci perilaku yang benar, terutama bahwa waktu hasil parsing
tersebar sepanjang durasi video.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest
from clipper_shared.subtitles.youtube import (
    SubtitleTrack,
    parse_json3,
    parse_srt,
    parse_subtitle,
    pick_track,
)


def _json3(payload: Mapping[str, object]) -> str:
    return json.dumps(payload)


class TestParseJson3Offsets:
    """``tOffsetMs`` WAJIB dijumlahkan dengan ``tStartMs`` event-nya."""

    def test_offset_relatif_terhadap_event(self) -> None:
        """Event di detik 60 dengan offset 500 ms harus menghasilkan 60,5 detik.

        Inilah bug aslinya: tanpa penjumlahan, kata ini muncul di detik 0,5 dan
        seluruh transkrip menumpuk di awal video.
        """
        payload = {
            "events": [
                {
                    "tStartMs": 60_000,
                    "dDurationMs": 2_000,
                    "segs": [{"utf8": "selamat", "tOffsetMs": 500, "dDurationMs": 800}],
                }
            ]
        }
        words = parse_json3(_json3(payload))
        assert len(words) == 1
        assert words[0].text == "selamat"
        assert words[0].start_s == pytest.approx(60.5)
        assert words[0].end_s == pytest.approx(61.3)

    def test_waktu_tersebar_sepanjang_video(self) -> None:
        """Regresi inti: kata tidak boleh menumpuk di beberapa detik pertama.

        Kasus nyata yang gagal: video 213 detik, transkrip 784 kata, tetapi
        rentang waktu hasil parsing hanya 0,1–3,9 detik.
        """
        events = []
        for index in range(40):
            # Satu event tiap 5 detik -> tersebar sampai 200 detik.
            events.append(
                {
                    "tStartMs": index * 5_000,
                    "dDurationMs": 4_000,
                    "segs": [
                        {"utf8": f"kata{index}a", "tOffsetMs": 0, "dDurationMs": 1_000},
                        {"utf8": f"kata{index}b", "tOffsetMs": 1_500, "dDurationMs": 1_000},
                    ],
                }
            )
        words = parse_json3(_json3({"events": events}))

        assert len(words) == 80
        first = min(w.start_s for w in words)
        last = max(w.start_s for w in words)
        assert first == pytest.approx(0.0)
        # Kata terakhir harus dekat akhir video, bukan di detik 4.
        assert last == pytest.approx(196.5)
        assert last > 190, f"waktu tidak tersebar: kata terakhir di {last}s"

    def test_event_tanpa_tstart_dilewati(self) -> None:
        """Tanpa acuan absolut, offset tidak bermakna — lebih baik dilewati."""
        payload = {
            "events": [
                {"segs": [{"utf8": "hantu", "tOffsetMs": 100}]},
                {"tStartMs": 10_000, "segs": [{"utf8": "nyata", "tOffsetMs": 0}]},
            ]
        }
        words = parse_json3(_json3(payload))
        assert [w.text for w in words] == ["nyata"]
        assert words[0].start_s == pytest.approx(10.0)

    def test_kata_tidak_melewati_akhir_event(self) -> None:
        """Durasi yang salah tidak boleh membuat kata melewati event-nya."""
        payload = {
            "events": [
                {
                    "tStartMs": 5_000,
                    "dDurationMs": 1_000,
                    "segs": [{"utf8": "panjang", "tOffsetMs": 0, "dDurationMs": 9_000}],
                }
            ]
        }
        words = parse_json3(_json3(payload))
        assert words[0].end_s == pytest.approx(6.0), "end_s harus dipotong di akhir event"

    def test_segmen_multi_kata_dibagi_merata(self) -> None:
        """Satu segmen berisi beberapa kata dibagi sepanjang durasinya."""
        payload = {
            "events": [
                {
                    "tStartMs": 20_000,
                    "segs": [{"utf8": "satu dua tiga", "tOffsetMs": 0, "dDurationMs": 900}],
                }
            ]
        }
        words = parse_json3(_json3(payload))
        assert [w.text for w in words] == ["satu", "dua", "tiga"]
        assert words[0].start_s == pytest.approx(20.0)
        assert words[0].end_s == pytest.approx(20.3)
        assert words[2].end_s == pytest.approx(20.9)

    def test_muatan_rusak_tidak_melempar(self) -> None:
        """JSON cacat dikembalikan sebagai daftar kosong, bukan exception."""
        assert parse_json3("bukan json") == []
        assert parse_json3("{}") == []
        assert parse_json3('{"events": "bukan list"}') == []

    def test_offset_teks_kosong_dilewati(self) -> None:
        payload = {
            "events": [
                {"tStartMs": 1_000, "segs": [{"utf8": "  ", "tOffsetMs": 0}, {"utf8": "isi", "tOffsetMs": 200}]}
            ]
        }
        words = parse_json3(_json3(payload))
        assert [w.text for w in words] == ["isi"]


class TestParseSrt:
    SRT = (
        "1\n"
        "00:00:01,000 --> 00:00:04,000\n"
        "Halo dunia\n"
        "\n"
        "2\n"
        "00:00:10,500 --> 00:00:13,250\n"
        "Baris kedua\n"
    )

    def test_waktu_benar(self) -> None:
        words = parse_srt(self.SRT)
        assert words, "SRT valid harus menghasilkan kata"
        assert words[0].start_s == pytest.approx(1.0)
        assert min(w.start_s for w in words) == pytest.approx(1.0)
        assert max(w.start_s for w in words) >= 10.0

    def test_vtt_dengan_header(self) -> None:
        vtt = "WEBVTT\n\n00:00:02.000 --> 00:00:05.000\nTeks vtt\n"
        words = parse_srt(vtt)
        assert words
        assert words[0].start_s == pytest.approx(2.0)


class TestPickTrack:
    def _track(self, lang: str, ext: str = "json3") -> SubtitleTrack:
        return SubtitleTrack(lang=lang, ext=ext, url=f"http://x/{lang}.{ext}")

    def test_memilih_trek_orig_lebih_dulu(self) -> None:
        """``-orig`` adalah bahasa asli pembicara, bukan terjemahan."""
        tracks = [self._track("en"), self._track("en-orig")]
        picked = pick_track(tracks, "en")
        assert picked is not None
        assert picked.lang == "en-orig"

    def test_json3_dipilih_untuk_timestamp_per_kata(self) -> None:
        tracks = [self._track("en", "srt"), self._track("en", "json3")]
        picked = pick_track(tracks, "en")
        assert picked is not None
        assert picked.ext == "json3"

    def test_daftar_kosong_mengembalikan_none(self) -> None:
        assert pick_track([], "en") is None

    def test_bahasa_pilihan_tanpa_trek_cocok_mengembalikan_none(self) -> None:
        """Job 'id' dengan subtitle Inggris saja → Whisper, bukan terjemahan."""
        tracks = [self._track("en"), self._track("en-orig")]
        assert pick_track(tracks, "id") is None


class TestParseSubtitleDispatch:
    def test_memilih_parser_dari_ekstensi(self) -> None:
        payload = _json3(
            {"events": [{"tStartMs": 1_000, "segs": [{"utf8": "x", "tOffsetMs": 0}]}]}
        )
        assert parse_subtitle(payload, "json3")

    def test_ekstensi_tidak_didukung_melempar(self) -> None:
        with pytest.raises(ValueError, match="tidak didukung"):
            parse_subtitle("x", "docx")


class TestLanguageAwareTrack:
    """Trek dipilih menurut bahasa ucapan, baru lalu manual > otomatis."""

    def test_bahasa_orig_mengalahkan_trek_manual_bahasa_lain(self) -> None:
        from clipper_shared.subtitles import SubtitleTrack, pick_track

        tracks = [
            SubtitleTrack(lang="ar", ext="json3", url="u"),
            SubtitleTrack(lang="en", ext="json3", url="u"),
            SubtitleTrack(lang="id", ext="json3", url="u"),
            SubtitleTrack(lang="id-orig", ext="json3", url="u", automatic=True),
        ]
        chosen = pick_track(tracks)
        assert chosen is not None and chosen.lang == "id" and not chosen.automatic

    def test_bahasa_dari_yt_dlp_dipakai(self) -> None:
        from clipper_shared.subtitles import SubtitleTrack, pick_track

        tracks = [SubtitleTrack(lang="ar", ext="json3", url="u"), SubtitleTrack(lang="en", ext="vtt", url="u")]
        chosen = pick_track(tracks, spoken_lang="en")
        assert chosen is not None and chosen.lang == "en"


def test_kata_json3_yang_menimpa_kata_berikutnya_dipotong() -> None:
    import json

    from clipper_shared.subtitles.youtube import parse_json3

    payload = json.dumps(
        {"events": [{"tStartMs": 0, "segs": [{"utf8": "panjangsekalikatanya"}, {"utf8": "lalu", "tOffsetMs": 300}]}]}
    )
    words = parse_json3(payload)
    assert words[0].end_s <= words[1].start_s
