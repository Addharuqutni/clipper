"""Test reframing: geometri crop, rantai filter, dan penghalusan posisi.

Penekanan pada tiga perilaku yang menentukan hasil terlihat benar atau tidak:

* deadband menahan getaran mikro;
* snap menangani ganti adegan tanpa luncuran lambat;
* posisi dipertahankan saat wajah hilang, bukan kembali ke tengah.
"""

from __future__ import annotations

import pytest
from clipper_shared.reframe.modes import (
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    CropMode,
    build_filter_chain,
    crop_width_for,
    describe_mode,
    plan_letterbox,
)
from clipper_shared.reframe.tracker import CropSmoother, mouth_is_speaking, score_face_candidate


class TestLetterbox:
    """Mode bar hitam & blur latar: video utuh dipusatkan di kanvas vertikal."""

    def test_landscape_source_fits_with_bars(self) -> None:
        geo = plan_letterbox(1920, 1080)
        # 16:9 di kanvas 9:16 -> lebar penuh, tinggi menyusut.
        assert geo.scaled_width == OUTPUT_WIDTH
        assert geo.scaled_height < OUTPUT_HEIGHT
        assert geo.needs_bar_fill

    def test_dimensions_are_even(self) -> None:
        # H.264 + yuv420p mensyaratkan dimensi genap; ganjil -> ffmpeg error.
        geo = plan_letterbox(1919, 1081)
        assert geo.scaled_width % 2 == 0
        assert geo.scaled_height % 2 == 0
        assert geo.offset_x % 2 == 0
        assert geo.offset_y % 2 == 0

    def test_portrait_source_fills_canvas(self) -> None:
        geo = plan_letterbox(1080, 1920)
        assert not geo.needs_bar_fill
        assert geo.offset_x == 0
        assert geo.offset_y == 0

    def test_centered_offsets(self) -> None:
        geo = plan_letterbox(1920, 1080)
        # Sisa ruang dibagi rata, lalu dibulatkan ke BAWAH ke bilangan genap
        # (syarat encoder yuv420p). Karena itu hasilnya boleh kurang 1 piksel
        # dari pembagian persis — pemusatan visualnya tidak terpengaruh.
        expected_x = (OUTPUT_WIDTH - geo.scaled_width) // 2
        expected_y = (OUTPUT_HEIGHT - geo.scaled_height) // 2
        assert geo.offset_x % 2 == 0 and geo.offset_x in {expected_x, expected_x - 1}
        assert geo.offset_y % 2 == 0 and geo.offset_y in {expected_y, expected_y - 1}

    def test_bars_are_roughly_symmetric(self) -> None:
        # Selisih kiri-kanan tidak boleh lebih dari 1 piksel, kalau tidak
        # pemusatan akan terlihat miring.
        geo = plan_letterbox(1920, 1080)
        right_gap = OUTPUT_WIDTH - (geo.offset_x + geo.scaled_width)
        assert abs(right_gap - geo.offset_x) <= 1

        bottom_gap = OUTPUT_HEIGHT - (geo.offset_y + geo.scaled_height)
        assert abs(bottom_gap - geo.offset_y) <= 1

    def test_zero_dimension_rejected(self) -> None:
        with pytest.raises(ValueError):
            plan_letterbox(0, 1080)


class TestCropWidth:
    def test_widescreen_uses_height_ratio(self) -> None:
        # 1080 * (1080/1920) = 607.5 -> dibulatkan ke bawah jadi 606 (genap).
        assert crop_width_for(1920, 1080) == 606

    def test_narrow_source_uses_full_width(self) -> None:
        # Sumber lebih sempit dari 9:16: rumus tanpa penjagaan akan negatif.
        width = crop_width_for(400, 1920)
        assert width == 400

    def test_never_negative(self) -> None:
        assert crop_width_for(100, 1920) >= 2


class TestFilterChain:
    def test_black_bars_uses_pad(self) -> None:
        geo = plan_letterbox(1920, 1080)
        chain = build_filter_chain(CropMode.BLACK_BARS, geo)
        assert "pad=" in chain
        assert "color=black" in chain

    def test_blurred_fill_splits_and_blurs(self) -> None:
        geo = plan_letterbox(1920, 1080)
        chain = build_filter_chain(CropMode.BLURRED_FILL, geo)
        assert "split=2" in chain
        assert "boxblur=" in chain
        assert "overlay=" in chain

    def test_face_track_crops(self) -> None:
        geo = plan_letterbox(1920, 1080)
        chain = build_filter_chain(CropMode.FACE_TRACK, geo)
        assert chain.startswith("crop=")
        # Posisi x dikendalikan pemanggil (dari berkas sendcmd).
        custom = build_filter_chain(CropMode.FACE_TRACK, geo, crop_x_expression="100")
        assert "crop=606:1080:100:0" in custom

    def test_every_mode_has_a_description(self) -> None:
        for mode in CropMode:
            assert describe_mode(mode)


class TestCropSmoother:
    def test_deadzone_ignores_tiny_movement(self) -> None:
        smoother = CropSmoother(crop_width=600, source_width=1920)
        start = smoother.smoothed_center
        # 1 px jauh di bawah deadband (2% * 600 = 12 px) -> tidak bergerak.
        smoother.update(start + 1)
        assert smoother.smoothed_center == start

    def test_ema_moves_partway_toward_target(self) -> None:
        smoother = CropSmoother(crop_width=600, source_width=1920, alpha=0.15)
        start = smoother.smoothed_center
        target = start + 100  # di atas deadband, di bawah snap
        smoother.update(target)
        moved = smoother.smoothed_center - start
        assert 0 < moved < 100

    def test_snap_on_scene_change(self) -> None:
        smoother = CropSmoother(crop_width=600, source_width=1920)
        # 300 px > snap (35% * 600 = 210 px) -> harus langsung pindah.
        smoother.update(1600.0)
        assert smoother.smoothed_center == 1600.0

    def test_holds_position_when_face_lost(self) -> None:
        smoother = CropSmoother(crop_width=600, source_width=1920)
        smoother.update(1600.0)
        held = smoother.smoothed_center
        # Wajah menghilang sesaat: pertahankan posisi, JANGAN kembali ke tengah.
        result = smoother.update(None)
        assert result == held
        assert smoother.missed_frames == 1

    def test_starts_centered_without_initial_center(self) -> None:
        smoother = CropSmoother(crop_width=600, source_width=1920)
        assert smoother.smoothed_center == 960.0

    def test_crop_x_clamped_inside_frame(self) -> None:
        smoother = CropSmoother(crop_width=600, source_width=1920)
        smoother.update(10.0)  # snap ke kiri
        assert smoother.crop_x() >= 0
        smoother.update(1910.0)  # snap ke kanan
        assert smoother.crop_x() <= 1920 - 600

    def test_crop_wider_than_source_pins_left(self) -> None:
        smoother = CropSmoother(crop_width=2000, source_width=1920)
        assert smoother.crop_x() == 0

    def test_zero_crop_width_rejected(self) -> None:
        with pytest.raises(ValueError):
            CropSmoother(crop_width=0, source_width=1920)


class TestFaceScoring:
    def test_larger_face_scores_higher(self) -> None:
        big = score_face_candidate(400, 960, 960, 1920, speaking=False)
        small = score_face_candidate(100, 960, 960, 1920, speaking=False)
        assert big > small

    def test_speaking_face_gets_bonus(self) -> None:
        silent = score_face_candidate(200, 960, 960, 1920, speaking=False)
        talking = score_face_candidate(200, 960, 960, 1920, speaking=True)
        assert talking > silent

    def test_speaking_bonus_can_beat_bigger_silent_face(self) -> None:
        # Inilah alasan bonus ada: pembicara yang duduk agak jauh tetap dipilih.
        far_speaking = score_face_candidate(150, 960, 960, 1920, speaking=True)
        near_silent = score_face_candidate(300, 960, 960, 1920, speaking=False)
        assert far_speaking > near_silent

    def test_continuity_favours_current_position(self) -> None:
        near = score_face_candidate(200, 970, 960, 1920, speaking=False)
        faraway = score_face_candidate(200, 1800, 960, 1920, speaking=False)
        assert near > faraway


class TestMouthOpenness:
    def test_closed_mouth_is_not_speaking(self) -> None:
        assert not mouth_is_speaking([100.0, 101.0], face_height=200.0)

    def test_open_mouth_is_speaking(self) -> None:
        # 60/200 = 0.30 > ambang 0.18
        assert mouth_is_speaking([100.0, 160.0], face_height=200.0)

    def test_insufficient_landmarks(self) -> None:
        assert not mouth_is_speaking([100.0], face_height=200.0)

    def test_zero_face_height(self) -> None:
        assert not mouth_is_speaking([100.0, 160.0], face_height=0.0)
