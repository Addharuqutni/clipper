"""Rentang unduhan siaran live harus jatuh di dalam jendela DVR.

Regresi yang dijaga: `-live_start_index` negatif menghitung offset dari **awal**
jendela, bukan dari ujung siaran, dan bagian yang melewati ujung live membuat
ffmpeg menunggu realtime sampai timeout membunuh job. Karena itu rentang harus
dinyatakan sebagai seek terhitung di dalam jendela yang tersedia via `--download-sections`.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
from pathlib import Path

if sys.path and str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _command(tmp_path: Path, monkeypatch, **kwargs: object) -> list[str]:
    """Jalankan download_youtube sampai perintah yt-dlp terbentuk, lalu tangkap."""
    from worker_light import media_fetcher

    captured: dict[str, list[str]] = {}

    def fake_run_process(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        return subprocess.CompletedProcess(command, 1, "", "warn: stop")

    monkeypatch.setattr(media_fetcher, "run_process", fake_run_process)
    # Unduhan "gagal" (returncode 1) supaya tidak ada berkas yang dicari; yang
    # diuji adalah perintah yang terbentuk, bukan hasil unduhannya.
    with contextlib.suppress(media_fetcher.IngestError):
        media_fetcher.download_youtube("https://youtu.be/x", tmp_path, job_id="job", **kwargs)  # type: ignore[arg-type]
    return captured["command"]


def _value(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


def test_rentang_live_diakhir_jendela_dvr(tmp_path: Path, monkeypatch) -> None:
    command = _command(tmp_path, monkeypatch, live_minutes=30, live_window_s=3600.0)

    # 30 menit terakhir dari jendela 1 jam: detik 1800-3600.
    assert _value(command, "--download-sections") == "*1800-3600"
    assert _value(command, "--downloader-args") == "ffmpeg_i:-live_start_index 0"


def test_rentang_lebih_panjang_dari_jendela_dipotong(tmp_path: Path, monkeypatch) -> None:
    command = _command(tmp_path, monkeypatch, live_minutes=60, live_window_s=900.0)

    # 15 menit terakhir = seluruh jendela; bukan 3600 detik yang tidak ada.
    assert _value(command, "--download-sections") == "*0-900"


def test_tanpa_jendela_tetap_mengunduh_dari_tepi_live(tmp_path: Path, monkeypatch) -> None:
    command = _command(tmp_path, monkeypatch, live_minutes=20)

    assert _value(command, "--download-sections") == "*0-1200"
    assert "--downloader-args" not in command


def test_siaran_selesai_tidak_memakai_rentang_live(tmp_path: Path, monkeypatch) -> None:
    command = _command(tmp_path, monkeypatch)

    assert "--download-sections" not in command


def test_playlist_duration_s_master_dan_media(monkeypatch) -> None:
    from worker_light import media_fetcher

    master_content = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=1280000\n"
        "media.m3u8\n"
    )
    media_content = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:1\n"
        "#EXTINF:1.5,\n"
        "seg1.ts\n"
        "#EXTINF:2.5,\n"
        "seg2.ts\n"
    )

    def fake_urlopen(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req
        if "master.m3u8" in url:
            return io.BytesIO(master_content.encode("utf-8"))
        if "media.m3u8" in url:
            return io.BytesIO(media_content.encode("utf-8"))
        raise urllib.error.URLError("not found")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    duration = media_fetcher._playlist_duration_s("https://example.com/live/master.m3u8")
    assert duration == 4.0


def test_playlist_duration_s_media_langsung_crlf(monkeypatch) -> None:
    from worker_light import media_fetcher

    media_crlf = "#EXTM3U\r\n#EXTINF:10.0,\r\nseg1.ts\r\n#EXTINF:5.0,\r\nseg2.ts\r\n"

    def fake_urlopen(req, timeout=10):
        return io.BytesIO(media_crlf.encode("utf-8"))

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    duration = media_fetcher._playlist_duration_s("https://example.com/media.m3u8")
    assert duration == 15.0


def test_playlist_duration_s_menolak_skema_tidak_aman() -> None:
    from worker_light import media_fetcher

    assert media_fetcher._playlist_duration_s("file:///etc/passwd") is None
    assert media_fetcher._playlist_duration_s("gopher://example.com") is None


def test_live_window_s_memilih_format_m3u8(monkeypatch) -> None:
    from worker_light import media_fetcher

    monkeypatch.setattr(media_fetcher, "_playlist_duration_s", lambda url: 900.0 if "valid" in url else None)

    formats = [
        {"protocol": "http_dash_segments", "url": "https://example.com/dash.mpd"},
        {"protocol": "m3u8_native", "url": "https://example.com/valid.m3u8"},
    ]
    assert media_fetcher._live_window_s(formats) == 900.0

