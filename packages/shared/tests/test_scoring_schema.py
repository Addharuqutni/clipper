"""Batas jumlah klip per durasi video (clipper_shared.scoring)."""

from __future__ import annotations

from clipper_shared.scoring import MAX_SEGMENTS


class TestMaxClipsForDuration:
    """Batas jumlah klip mengikuti durasi video: 1 klip per 3 menit, 1–30."""

    def test_video_pendek_tetap_boleh_satu_klip(self) -> None:
        from clipper_shared.scoring import max_clips_for_duration

        assert max_clips_for_duration(0) == 1
        assert max_clips_for_duration(5 * 60) == 1

    def test_bertambah_sesuai_durasi(self) -> None:
        from clipper_shared.scoring import max_clips_for_duration

        assert max_clips_for_duration(15 * 60) == 5
        assert max_clips_for_duration(60 * 60) == 20

    def test_dibatasi_maksimum_global(self) -> None:
        from clipper_shared.scoring import max_clips_for_duration

        assert max_clips_for_duration(180 * 60) == MAX_SEGMENTS
