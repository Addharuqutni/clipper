"""Test pipeline worker-render: pipe FFmpeg aman, reframer, dan detektor wajah.

Modul-modul ini bergantung pada FFmpeg dan MediaPipe yang tidak tersedia di
lingkungan uji. Karena itu test di sini memusatkan diri pada hal yang **dapat**
diperiksa tanpa keduanya, dan justru hal itulah yang paling sering salah:

* penanganan jalur berkas FFmpeg (titik dua Windows adalah jebakan nyata);
* pembentukan berkas perintah posisi crop (aritmetika + pengurangan ukuran);
* penjagaan terhadap proses yang macet (watchdog harus benar-benar mematikan);
* logika pemilihan subjek dan ambang bicara.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from worker_render.reframer import ReframeOptions, SourceInfo

WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))


class TestFilterPathEscaping:
    """Jebakan Windows: titik dua pada jalur dibaca FFmpeg sebagai pemisah opsi.

    Ada DUA bentuk pemakaian yang memerlukan escape BERBEDA, dan ini pernah
    menjadi bug dua kali:

    * ``sendcmd=f=...`` (tanpa kutip) → titik dua di-escape **ganda** (``\\\\:``);
    * ``ass='...'`` (kutip tunggal) → titik dua di-escape **sekali** (``\\:``).

    Test lama hanya memeriksa ``"\\\\:" in escaped`` — yang juga lolos untuk
    varian yang salah. Sekarang nilai persisnya diuji, dan keduanya dijalankan
    terhadap binary FFmpeg sungguhan.
    """

    def test_sendcmd_escape_ganda(self) -> None:
        """Opsi tanpa kutip butuh escape titik dua GANDA."""
        from worker_render.reframer import escape_filter_path

        assert escape_filter_path(Path("C:/project/work/cmd.txt")) == "C\\\\:/project/work/cmd.txt"

    def test_ass_escape_tunggal(self) -> None:
        """Opsi berkutip tunggal butuh escape titik dua SEKALI.

        Memakai varian ganda di sini menghasilkan error yang menyebut opsi lain
        sama sekali (``original_size``), sehingga penyebabnya sulit terlihat.
        """
        from worker_render.reframer import quote_filter_path

        assert quote_filter_path(Path("C:/project/work/captions.ass")) == "C\\:/project/work/captions.ass"

    def test_kedua_fungsi_berbeda(self) -> None:
        """Keduanya tidak boleh bertukar — perbedaan inilah inti perbaikannya."""
        from worker_render.reframer import escape_filter_path, quote_filter_path

        path = Path("C:/x/y.ass")
        assert escape_filter_path(path) != quote_filter_path(path)

    def test_tidak_menyisakan_garis_miring_dibalik(self) -> None:
        """Garis miring dibalik apa pun selain milik escape titik dua harus hilang."""
        from worker_render.reframer import escape_filter_path, quote_filter_path

        for fn in (escape_filter_path, quote_filter_path):
            escaped = fn(Path("C:\\project\\work\\captions.ass"))
            assert "\\" not in escaped.replace("\\\\:", "").replace("\\:", "")

    def test_posix_path_unchanged(self) -> None:
        from worker_render.reframer import escape_filter_path, quote_filter_path

        assert escape_filter_path(Path("/tmp/work/a.ass")) == "/tmp/work/a.ass"
        assert quote_filter_path(Path("/tmp/work/a.ass")) == "/tmp/work/a.ass"

    def test_berfungsi_nyata_di_ffmpeg(self, tmp_path: Path) -> None:
        """Bukan sekadar bentuk string: rantai filter harus benar-benar dijalankan.

        Test string di atas bisa lulus sementara FFmpeg tetap menolak. Test ini
        menutup celah itu dengan menjalankan FFmpeg sungguhan untuk KEDUA bentuk.
        """
        import shutil
        import subprocess

        ffmpeg = os.getenv("FFMPEG_BINARY") or shutil.which("ffmpeg")
        if not ffmpeg:
            pytest.skip("FFmpeg tidak tersedia di lingkungan ini")

        from worker_render.reframer import escape_filter_path, quote_filter_path

        source = tmp_path / "src.mp4"
        probe = subprocess.run(  # noqa: S603
            [ffmpeg, "-v", "error", "-y", "-f", "lavfi",
             "-i", "testsrc=size=1920x1080:rate=30:duration=1",
             "-c:v", "libx264", "-preset", "ultrafast", str(source)],
            capture_output=True, text=True, check=False, timeout=120,
        )
        if probe.returncode != 0:
            pytest.skip(f"tidak dapat membuat video uji: {probe.stderr[-200:]}")

        cmds = tmp_path / "crop_positions.txt"
        cmds.write_text("0.5 crop x 150;\n", encoding="utf-8")
        ass = tmp_path / "captions.ass"
        ass.write_text(
            "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour,"
            " OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY,"
            " Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR,"
            " MarginV, Encoding\n"
            "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,"
            "100,100,0,0,1,2,0,2,10,10,10,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV,"
            " Effect, Text\n"
            "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Halo\n",
            encoding="utf-8",
        )

        chains = {
            "sendcmd": (
                f"sendcmd=f={escape_filter_path(cmds)},crop=606:1080:0:0,scale=540:960"
            ),
            "ass": f"ass='{quote_filter_path(ass)}'",
        }
        for name, chain in chains.items():
            output = tmp_path / f"out_{name}.mp4"
            result = subprocess.run(  # noqa: S603
                [ffmpeg, "-v", "error", "-y", "-i", str(source), "-vf", chain,
                 "-frames:v", "2", str(output)],
                capture_output=True, text=True, check=False, timeout=240,
            )
            assert result.returncode == 0, (
                f"FFmpeg menolak rantai filter '{name}'. Pesan sering menyebut teks "
                "SETELAH titik dua, sehingga penyebabnya (escape titik dua drive) "
                f"tidak terlihat:\n{result.stderr[-400:]}"
            )
            assert output.is_file()


class TestSubtitlesBurnedInSinglePass:
    """``render_segment(subtitles=...)`` harus memakai escape yang benar untuk ``ass=``.

    Test fungsi escape di atas memeriksa keluaran fungsi, bukan **pemakaiannya**.
    Test ini menjalankan render sungguhan dengan subtitle sehingga kesalahan
    memakai ``escape_filter_path`` (varian ganda) di tempat ``ass=`` ketahuan.
    """

    def test_render_dengan_subtitle_berhasil(self, tmp_path: Path) -> None:
        import shutil
        import subprocess

        ffmpeg = os.getenv("FFMPEG_BINARY") or shutil.which("ffmpeg")
        if not ffmpeg:
            pytest.skip("FFmpeg tidak tersedia di lingkungan ini")

        from clipper_shared.reframe import CropMode
        from clipper_shared.subtitles import SubtitleStyle, SubtitleWord
        from worker_render.reframer import ReframeOptions, render_segment, write_subtitles

        source = tmp_path / "src.mp4"
        probe = subprocess.run(  # noqa: S603
            [ffmpeg, "-v", "error", "-y", "-f", "lavfi",
             "-i", "testsrc=size=1280x720:rate=30:duration=1",
             "-c:v", "libx264", "-preset", "ultrafast", str(source)],
            capture_output=True, text=True, check=False, timeout=120,
        )
        if probe.returncode != 0:
            pytest.skip(f"tidak dapat membuat video uji: {probe.stderr[-200:]}")

        options = ReframeOptions(
            mode=CropMode.BLACK_BARS,
            start_s=0.0,
            end_s=1.0,
            output_width=540,
            output_height=960,
            preset="ultrafast",
            crf=30,
            ffmpeg_threads=1,
        )
        ass = write_subtitles(
            [SubtitleWord(text="halo", start_s=0.0, end_s=0.4),
             SubtitleWord(text="dunia", start_s=0.4, end_s=0.8)],
            tmp_path,
            options=options,
            style=SubtitleStyle(),
        )
        output = tmp_path / "subbed.mp4"
        render_segment(source, output, options, work_dir=tmp_path, subtitles=ass)
        assert output.is_file() and output.stat().st_size > 0


class TestSendCmdFile:
    """Berkas perintah `sendcmd` harus ringkas dan tepat waktu."""

    def test_skips_unchanged_positions(self, tmp_path: Path) -> None:
        from worker_render.reframer import write_sendcmd_file

        positions = [100, 100, 100, 200, 200]
        path = write_sendcmd_file(positions, tmp_path / "cmd.txt", fps=30.0)
        lines = path.read_text(encoding="utf-8").strip().splitlines()

        # Hanya dua nilai berbeda -> dua perintah, bukan lima.
        assert len(lines) == 2
        assert lines[0].startswith("0.0000 crop x 100;")
        # Perubahan kedua terjadi pada indeks 3 -> 3/30 = 0,1 detik.
        assert lines[1].startswith("0.1000 crop x 200;")

    def test_timestamps_use_fps(self, tmp_path: Path) -> None:
        from worker_render.reframer import write_sendcmd_file

        path = write_sendcmd_file([10, 20], tmp_path / "cmd.txt", fps=10.0)
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert lines[1].startswith("0.1000 ")

    def test_empty_positions_writes_empty_file(self, tmp_path: Path) -> None:
        from worker_render.reframer import write_sendcmd_file

        path = write_sendcmd_file([], tmp_path / "cmd.txt", fps=30.0)
        assert path.read_text(encoding="utf-8") == "\n"

    def test_keeps_command_count_bounded(self, tmp_path: Path) -> None:
        # Klip 60 detik @30fps = 1800 frame. Berkas perintah harus jauh lebih
        # pendek daripada satu baris per frame, kalau tidak FFmpeg lambat membaca.
        from worker_render.reframer import write_sendcmd_file

        positions = [100 + (index // 15) for index in range(1800)]
        path = write_sendcmd_file(positions, tmp_path / "cmd.txt", fps=30.0)
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) <= 130  # ~120 perubahan, bukan 1800


class TestFractionParsing:
    """fps dari ffprobe adalah pecahan: 30000/1001 = 29,97."""

    def test_ntsc_fraction(self) -> None:
        from worker_render.reframer import _parse_fraction

        assert _parse_fraction("30000/1001") == pytest.approx(29.97, abs=0.01)

    def test_plain_integer(self) -> None:
        from worker_render.reframer import _parse_fraction

        assert _parse_fraction("25") == 25.0

    def test_zero_denominator_does_not_crash(self) -> None:
        from worker_render.reframer import _parse_fraction

        assert _parse_fraction("30/0") == 0.0

    def test_garbage_returns_zero(self) -> None:
        from worker_render.reframer import _parse_fraction

        assert _parse_fraction("abc") == 0.0


class TestFilterChainPerMode:
    """Tiga mode harus menghasilkan rantai filter yang benar-benar berbeda."""

    def _info(self) -> SourceInfo:
        from worker_render.reframer import SourceInfo

        return SourceInfo(
            width=1920, height=1080, fps=30.0, duration_s=60.0, has_audio=True, frame_count=1800
        )

    def _options(self, mode: str) -> ReframeOptions:
        from clipper_shared.reframe import CropMode
        from worker_render.reframer import ReframeOptions

        return ReframeOptions(mode=CropMode(mode), start_s=0.0, end_s=5.0)

    def test_black_bars_uses_pad(self, tmp_path: Path) -> None:
        from worker_render.reframer import build_video_filter

        chain, health = build_video_filter(
            self._info(), self._options("black_bars"), tmp_path, source_path=tmp_path / "source.mp4"
        )
        assert "pad=" in chain
        assert health == ""  # tidak ada pelacakan untuk mode ini

    def test_blurred_fill_uses_split_and_blur(self, tmp_path: Path) -> None:
        from worker_render.reframer import build_video_filter

        chain, _ = build_video_filter(
            self._info(), self._options("blurred_fill"), tmp_path, source_path=tmp_path / "source.mp4"
        )
        assert "split=2" in chain
        assert "boxblur=" in chain

    def test_face_track_without_detection_falls_back_to_static_center(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Bila tidak ada wajah sama sekali, crop tengah statis dipakai — bukan
        # crop yang menempel di posisi awal.
        import worker_render.reframer as reframer
        from worker_render.reframer import build_video_filter

        monkeypatch.setattr(reframer, "compute_crop_positions", lambda *a, **k: ([], "tidak ada wajah"))

        chain, health = build_video_filter(
            self._info(), self._options("face_track"), tmp_path, source_path=tmp_path / "source.mp4"
        )
        assert "crop=" in chain
        assert "(in_w-out_w)/2" in chain  # benar-benar dipusatkan
        assert health == "tidak ada wajah"

    def test_face_track_with_detection_uses_sendcmd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import worker_render.reframer as reframer
        from worker_render.reframer import build_video_filter

        monkeypatch.setattr(
            reframer, "compute_crop_positions", lambda *a, **k: ([100, 120, 140], "bagus")
        )

        chain, health = build_video_filter(
            self._info(), self._options("face_track"), tmp_path, source_path=tmp_path / "source.mp4"
        )
        assert "sendcmd=" in chain
        assert health == "bagus"
        # Berkas perintah benar-benar ditulis ke disk.
        assert (tmp_path / "crop_positions.txt").exists()


class TestFaceSelection:
    """Pemilihan subjek: ukuran + bonus bicara - penalti jarak."""

    def _face(self, center: float, width: float, speaking: bool):  # type: ignore[no-untyped-def]
        from worker_render.face_tracker import FaceObservation

        return FaceObservation(
            center_x=center,
            center_y=540.0,
            width=width,
            height=width * 1.3,
            speaking=speaking,
            openness_ratio=0.3 if speaking else 0.01,
        )

    def test_empty_list_returns_none(self) -> None:
        from worker_render.face_tracker import choose_subject

        assert choose_subject([], current_center_x=960, source_width=1920) is None

    def test_larger_face_wins_when_otherwise_equal(self) -> None:
        from worker_render.face_tracker import choose_subject

        big = self._face(960, 400, False)
        small = self._face(960, 100, False)
        assert choose_subject([big, small], current_center_x=960, source_width=1920) is big

    def test_speaking_face_beats_slightly_larger_silent_face(self) -> None:
        from worker_render.face_tracker import choose_subject

        speaking = self._face(960, 200, True)
        silent = self._face(960, 260, False)
        assert choose_subject([speaking, silent], current_center_x=960, source_width=1920) is speaking

    def test_continuity_keeps_current_subject(self) -> None:
        from worker_render.face_tracker import choose_subject

        near = self._face(970, 200, False)
        far = self._face(1800, 200, False)
        assert choose_subject([near, far], current_center_x=960, source_width=1920) is near


class TestMouthOpenness:
    def test_threshold_marks_speaking(self) -> None:
        from worker_render.face_tracker import MIN_SPEAK_RATIO

        assert MIN_SPEAK_RATIO == 0.18

    def test_lip_indices_are_nonempty_and_within_landmark_range(self) -> None:
        from worker_render.face_tracker import LIP_INDICES

        assert len(LIP_INDICES) >= 20
        assert all(0 <= index < 478 for index in LIP_INDICES)
