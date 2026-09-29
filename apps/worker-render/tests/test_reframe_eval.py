"""Test fungsi penilaian presisi reframing (``worker_render.reframe_eval``).

Semua di sini **murni** — tanpa video, tanpa MediaPipe. Itu memang tujuannya:
keputusan HIT/MISS adalah bagian yang paling mudah salah di tepi (batas
jendela, margin, crop lebih lebar daripada sumber), dan kesalahan tepi itulah
yang bisa membalik angka "presisi reframing" yang dilaporkan ke pemilik produk.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from worker_render.reframe_eval import ClipEvaluation, ClipLabel

WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))

#: Sumber 1920x1080 → crop 9:16 memakai tinggi penuh: 1080 * 1080/1920 = 607.5
#: → dibulatkan ke bawah ke bilangan genap = 606 (lihat ``crop_width_for``).
SOURCE_W = 1920
CROP_W = 606


class TestScoreKeyframeBoundary:
    """Batas jendela crop: inklusif di kedua tepi, dan di luar itu MISS."""

    def test_wajah_di_tengah_hit(self) -> None:
        from worker_render.reframe_eval import score_keyframe

        score = score_keyframe(0.5, t=1.0, crop_x=657, crop_w=CROP_W, source_width=SOURCE_W)
        assert score.hit
        assert score.margin_hit

    def test_tepi_kiri_jendela_hit(self) -> None:
        """Batas kiri dihitung HIT: piksel itu sendiri masih di dalam crop."""
        from worker_render.reframe_eval import score_keyframe

        crop_x = 657
        score = score_keyframe(
            crop_x / SOURCE_W, t=0.0, crop_x=crop_x, crop_w=CROP_W, source_width=SOURCE_W
        )
        assert score.hit

    def test_tepi_kanan_jendela_hit(self) -> None:
        from worker_render.reframe_eval import score_keyframe

        crop_x = 657
        score = score_keyframe(
            (crop_x + CROP_W) / SOURCE_W,
            t=0.0,
            crop_x=crop_x,
            crop_w=CROP_W,
            source_width=SOURCE_W,
        )
        assert score.hit

    def test_satu_piksel_di_luar_kiri_miss(self) -> None:
        from worker_render.reframe_eval import score_keyframe

        crop_x = 657
        score = score_keyframe(
            (crop_x - 1) / SOURCE_W, t=0.0, crop_x=crop_x, crop_w=CROP_W, source_width=SOURCE_W
        )
        assert not score.hit

    def test_satu_piksel_di_luar_kanan_miss(self) -> None:
        from worker_render.reframe_eval import score_keyframe

        crop_x = 657
        score = score_keyframe(
            (crop_x + CROP_W + 1) / SOURCE_W,
            t=0.0,
            crop_x=crop_x,
            crop_w=CROP_W,
            source_width=SOURCE_W,
        )
        assert not score.hit

    def test_margin_menolak_wajah_di_tepi(self) -> None:
        """Di tepi crop: HIT longgar, tetapi bukan HIT bermargin."""
        from worker_render.reframe_eval import score_keyframe

        crop_x = 657
        score = score_keyframe(
            (crop_x + 1) / SOURCE_W, t=0.0, crop_x=crop_x, crop_w=CROP_W, source_width=SOURCE_W
        )
        assert score.hit
        assert not score.margin_hit

    def test_margin_menerima_wajah_di_dalam_80_persen(self) -> None:
        """25% masuk dari tepi kiri masih di dalam margin 10% per tepi."""
        from worker_render.reframe_eval import score_keyframe

        crop_x = 657
        score = score_keyframe(
            (crop_x + 0.25 * CROP_W) / SOURCE_W,
            t=0.0,
            crop_x=crop_x,
            crop_w=CROP_W,
            source_width=SOURCE_W,
        )
        assert score.hit
        assert score.margin_hit

    def test_margin_menolak_wajah_5_persen_dari_tepi(self) -> None:
        """5% dari tepi kiri: di dalam crop, tetapi di luar 80% tengah."""
        from worker_render.reframe_eval import score_keyframe

        crop_x = 657
        score = score_keyframe(
            (crop_x + 0.05 * CROP_W) / SOURCE_W,
            t=0.0,
            crop_x=crop_x,
            crop_w=CROP_W,
            source_width=SOURCE_W,
        )
        assert score.hit
        assert not score.margin_hit

    def test_margin_nol_sama_dengan_hit_longgar(self) -> None:
        from worker_render.reframe_eval import score_keyframe

        crop_x = 657
        score = score_keyframe(
            (crop_x + 559) / SOURCE_W,
            t=0.0,
            crop_x=crop_x,
            crop_w=CROP_W,
            source_width=SOURCE_W,
            margin_frac=0.0,
        )
        assert score.hit and score.margin_hit


class TestScoreKeyframeGeometry:
    """Kasus geometri yang tidak biasa, tetapi nyata pada video portrait/HP."""

    def test_crop_dijepit_ke_dalam_sumber(self) -> None:
        """``crop_x`` negatif atau melewati tepi diperlakukan sebagai terjepit."""
        from worker_render.reframe_eval import score_keyframe

        score = score_keyframe(0.0, t=0.0, crop_x=-100, crop_w=500, source_width=1000)
        assert score.crop_x == 0

        score = score_keyframe(1.0, t=0.0, crop_x=900, crop_w=500, source_width=1000)
        assert score.crop_x == 500

    def test_sumber_lebih_sempit_dari_crop(self) -> None:
        """Video portrait sempit: crop > sumber → jendela efektif = seluruh sumber.

        Tanpa penjepitan ini, ``left + crop_w`` melewati lebar sumber dan wajah
        di tepi kanan akan dinilai MISS padahal terlihat di klip.
        """
        from worker_render.reframe_eval import score_keyframe

        score = score_keyframe(1.0, t=0.0, crop_x=0, crop_w=1200, source_width=1080)
        assert score.crop_w == 1080
        assert score.hit

    def test_x_px_dihitung_dari_cx(self) -> None:
        """cx 0.25 pada sumber 1920 = piksel 480 — pas di tepi kanan jendela."""
        from worker_render.reframe_eval import score_keyframe

        score = score_keyframe(0.25, t=2.5, crop_x=0, crop_w=480, source_width=1920)
        assert score.x_px == pytest.approx(480.0)
        assert score.t == 2.5
        assert score.crop_x == 0  # 0 + 480 masih di dalam bingkai, tidak dijepit
        assert score.hit  # tepi kanan dihitung HIT (batas inklusif)
        assert not score.margin_hit  # tetapi tepat di tepi, bukan 80% tengah

    def test_cx_di_luar_0_1_ditolak(self) -> None:
        from worker_render.reframe_eval import score_keyframe

        with pytest.raises(ValueError):
            score_keyframe(1.2, t=0.0, crop_x=0, crop_w=100, source_width=1920)

    def test_margin_tidak_masuk_akal_ditolak(self) -> None:
        from worker_render.reframe_eval import score_keyframe

        with pytest.raises(ValueError):
            score_keyframe(0.5, t=0.0, crop_x=0, crop_w=100, source_width=1920, margin_frac=1.0)


class TestFrameSampling:
    """Keyframe jatuh ke frame yang benar pada lintasan per frame."""

    def test_frame_index_floor_dengan_epsilon(self) -> None:
        """30 fps: t=4.2 harus frame 126, bukan 125 karena pembulatan float."""
        from worker_render.reframe_eval import frame_index_for

        assert frame_index_for(4.2, 30.0, 1000) == 126
        assert frame_index_for(1.0 / 30.0, 30.0, 1000) == 1
        assert frame_index_for(0.0, 30.0, 1000) == 0

    def test_frame_index_dijepit_ke_lintasan(self) -> None:
        from worker_render.reframe_eval import frame_index_for

        assert frame_index_for(999.0, 30.0, 300) == 299

    def test_sample_crop_x_memakai_posisi_frame_tersebut(self) -> None:
        from worker_render.reframe_eval import sample_crop_x

        positions = [0, 10, 20, 30]  # 10 fps: t=0.25 → frame 2
        assert sample_crop_x(positions, 10.0, 0.25) == 20
        assert sample_crop_x(positions, 10.0, 2.0) == 30

    def test_lintasan_kosong_ditolak(self) -> None:
        from worker_render.reframe_eval import sample_crop_x

        with pytest.raises(ValueError):
            sample_crop_x([], 30.0, 1.0)


class TestEvaluateClip:
    """Agregasi per klip, termasuk fallback crop tengah saat tidak ada wajah."""

    def _label(self, *keyframes: tuple[float, float]) -> ClipLabel:
        from worker_render.reframe_eval import ClipLabel, KeyframeLabel

        return ClipLabel(
            clip="contoh.mp4",
            keyframes=tuple(KeyframeLabel(t=t, cx=cx) for t, cx in keyframes),
        )

    def test_hit_rate_dua_dari_tiga(self) -> None:
        from worker_render.reframe_eval import evaluate_clip

        label = self._label((0.0, 0.5), (0.1, 0.5), (0.2, 0.9))
        evaluation = evaluate_clip(
            label,
            positions=[657, 657, 657, 657],
            fps=10.0,
            source_width=SOURCE_W,
            source_height=1080,
            crop_w=CROP_W,
        )
        assert evaluation.keyframes_total == 3
        assert evaluation.hits == 2
        assert evaluation.precision == pytest.approx(2 / 3)

    def test_tanpa_wajah_memakai_crop_tengah_statis(self) -> None:
        """Reframer mengembalikan daftar kosong → penilaian harus memakai tengah.

        Ini perilaku nyata (``build_video_filter``: ``crop=...:(in_w-out_w)/2``),
        jadi klip tanpa wajah tidak boleh otomatis dianggap 0%.
        """
        from worker_render.reframe_eval import evaluate_clip

        label = self._label((0.0, 0.5))
        evaluation = evaluate_clip(
            label,
            positions=[],
            fps=30.0,
            source_width=SOURCE_W,
            source_height=1080,
            crop_w=CROP_W,
            tracking_health="TIDAK ADA wajah terdeteksi",
        )
        assert evaluation.no_face
        assert evaluation.hits == 1
        assert evaluation.scores[0].crop_x == (SOURCE_W - CROP_W) // 2

    def test_keyframe_di_luar_lintasan_dihitung_miss_walau_crop_terakhir_mencakup_wajah(self) -> None:
        """Frame terakhir mencakup cx=0.5, tetapi t=99 tidak punya crop → MISS.

        Menjepit ke frame terakhir akan membuat label yang salah waktu
        menaikkan presisi yang dilaporkan.
        """
        from worker_render.reframe_eval import evaluate_clip

        label = self._label((0.0, 0.5), (99.0, 0.5))
        evaluation = evaluate_clip(
            label,
            positions=[657, 657],
            fps=30.0,
            source_width=SOURCE_W,
            source_height=1080,
            crop_w=CROP_W,
        )
        assert evaluation.keyframes_total == 2
        assert [s.hit for s in evaluation.scores] == [True, False]
        assert not evaluation.scores[1].margin_hit
        assert evaluation.precision == 0.5

    def test_batas_lintasan_frame_terakhir_masih_dinilai(self) -> None:
        """10 fps, 4 frame: t=0.3 → frame 3 (ada), t=0.4 → frame 4 (tidak ada)."""
        from worker_render.reframe_eval import evaluate_clip

        label = self._label((0.3, 0.5), (0.4, 0.5))
        evaluation = evaluate_clip(
            label,
            positions=[657, 657, 657, 657],
            fps=10.0,
            source_width=SOURCE_W,
            source_height=1080,
            crop_w=CROP_W,
        )
        assert [s.hit for s in evaluation.scores] == [True, False]

    def test_tanpa_wajah_keyframe_melewati_durasi_dihitung_miss(self) -> None:
        """Crop tengah statis berlaku di seluruh klip, tetapi tidak sesudah klip berakhir."""
        from worker_render.reframe_eval import evaluate_clip

        label = self._label((1.0, 0.5), (5.0, 0.5))
        evaluation = evaluate_clip(
            label,
            positions=[],
            fps=30.0,
            source_width=SOURCE_W,
            source_height=1080,
            crop_w=CROP_W,
            duration_s=5.0,
        )
        assert [s.hit for s in evaluation.scores] == [True, False]


class TestLoadLabels:
    """Pembacaan ``labels.json`` + pesan error yang menuntun."""

    def test_membaca_labels_valid(self, tmp_path: Path) -> None:
        from worker_render.reframe_eval import load_labels

        (tmp_path / "labels.json").write_text(
            json.dumps(
                [
                    {"clip": "a.mp4", "keyframes": [{"t": 1.5, "cx": 0.25}, {"t": 2, "cx": 1}]},
                    {"clip": "b.mp4", "keyframes": [{"t": 0.0, "cx": 0.5}]},
                ]
            ),
            encoding="utf-8",
        )
        labels = load_labels(tmp_path)
        assert len(labels) == 2
        assert labels[0].clip == "a.mp4"
        assert labels[0].keyframes[0].t == 1.5
        assert labels[0].keyframes[1].cx == 1.0

    def test_cx_di_luar_rentang_ditolak_dengan_nama_lokasi(self, tmp_path: Path) -> None:
        from worker_render.reframe_eval import load_labels

        (tmp_path / "labels.json").write_text(
            json.dumps([{"clip": "a.mp4", "keyframes": [{"t": 1.0, "cx": 1.4}]}]),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="cx"):
            load_labels(tmp_path)

    def test_keyframes_kosong_ditolak(self, tmp_path: Path) -> None:
        from worker_render.reframe_eval import load_labels

        (tmp_path / "labels.json").write_text(
            json.dumps([{"clip": "a.mp4", "keyframes": []}]), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="keyframes"):
            load_labels(tmp_path)

    def test_json_bukan_daftar_ditolak(self, tmp_path: Path) -> None:
        from worker_render.reframe_eval import load_labels

        (tmp_path / "labels.json").write_text('{"clip": "a.mp4"}', encoding="utf-8")
        with pytest.raises(ValueError, match="daftar"):
            load_labels(tmp_path)

    def test_berkas_tidak_ada_pesan_menuntun(self, tmp_path: Path) -> None:
        from worker_render.reframe_eval import load_labels

        with pytest.raises(FileNotFoundError, match="labels.json"):
            load_labels(tmp_path)


class TestDatasetReportClaimGate:
    """Gerbang "belum boleh diklaim" (TECH_SPEC §5.2: minimal 20 klip)."""

    def _clip_eval(self, hits: int, total: int) -> ClipEvaluation:
        from worker_render.reframe_eval import ClipEvaluation, KeyframeScore

        scores = tuple(
            KeyframeScore(
                t=float(i),
                cx=0.5,
                x_px=960.0,
                crop_x=657,
                crop_w=CROP_W,
                source_width=SOURCE_W,
                hit=i < hits,
                margin_hit=i < hits,
            )
            for i in range(total)
        )
        return ClipEvaluation(
            clip=f"clip{total}.mp4",
            source_width=SOURCE_W,
            source_height=1080,
            fps=30.0,
            crop_w=CROP_W,
            tracking_health="",
            no_face=False,
            scores=scores,
        )

    def test_kurang_dari_20_klip_belum_boleh_diklaim(self) -> None:
        from worker_render.reframe_eval import MIN_CLIPS_FOR_CLAIM, DatasetReport

        report = DatasetReport(
            dataset="eval/reframe",
            margin_frac=0.2,
            clips=tuple(self._clip_eval(1, 1) for _ in range(MIN_CLIPS_FOR_CLAIM - 1)),
        )
        assert not report.claimable
        assert report.clips_total == MIN_CLIPS_FOR_CLAIM - 1

    def test_20_klip_boleh_diklaim_dan_presisi_diagregasi(self) -> None:
        from worker_render.reframe_eval import MIN_CLIPS_FOR_CLAIM, DatasetReport

        clips = tuple(self._clip_eval(1, 1) for _ in range(MIN_CLIPS_FOR_CLAIM))
        report = DatasetReport(dataset="eval/reframe", margin_frac=0.2, clips=clips)
        assert report.claimable
        assert report.precision == 1.0
        assert report.meets_target

    def test_presisi_global_adalah_total_hit_per_total_keyframe(self) -> None:
        """Bukan rata-rata presisi per klip — klip dengan 1 keyframe tidak boleh berbobot sama."""
        from worker_render.reframe_eval import DatasetReport

        report = DatasetReport(
            dataset="eval/reframe",
            margin_frac=0.2,
            clips=(self._clip_eval(1, 1), self._clip_eval(0, 9)),
        )
        assert report.keyframes_total == 10
        assert report.precision == pytest.approx(0.1)

    def test_dataset_kosong_tidak_meledak(self) -> None:
        from worker_render.reframe_eval import DatasetReport

        report = DatasetReport(dataset="eval/reframe", margin_frac=0.2, clips=())
        assert report.precision == 0.0
        assert not report.meets_target
