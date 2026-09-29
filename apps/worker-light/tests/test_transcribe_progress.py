"""Progres tahap transkripsi: naik terus, mengikuti posisi audio, dengan sisa waktu.

Dulu ingest berakhir di 70% lalu transkripsi melompat MUNDUR ke 25% dan diam
di 40% sampai Whisper selesai — belasan menit tanpa tanda kemajuan untuk video
40 menit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))

from worker_light.tasks import (  # noqa: E402
    ETA_MIN_AUDIO_S,
    TRANSCRIBE_END,
    TRANSCRIBE_START,
    transcribe_progress,
)


def test_progres_naik_monoton_dari_awal_ke_akhir_rentang() -> None:
    total = 2400.0
    values = [transcribe_progress(done, total, elapsed_s=done / 3)[0] for done in range(0, 2401, 30)]

    assert values[0] == TRANSCRIBE_START
    assert values[-1] == TRANSCRIBE_END
    assert values == sorted(values)


def test_posisi_melewati_total_tidak_melampaui_rentang() -> None:
    # Segmen terakhir Whisper bisa berakhir sedikit setelah durasi terlapor.
    assert transcribe_progress(2410.0, 2400.0, elapsed_s=800.0)[0] == TRANSCRIBE_END


def test_total_tidak_diketahui_tetap_di_awal() -> None:
    progress, message = transcribe_progress(30.0, 0.0, elapsed_s=10.0)
    assert progress == TRANSCRIBE_START
    assert "0:30 / 0:00" in message


def test_sisa_waktu_dari_kecepatan_aktual() -> None:
    # 10 menit audio dalam 200 dtk → 3x realtime; sisa 30 menit audio ≈ 10 menit.
    _, message = transcribe_progress(600.0, 2400.0, elapsed_s=200.0)
    assert message == "Transkripsi 10:00 / 40:00 · ±10 menit lagi"


def test_sisa_waktu_diukur_dari_patokan_bukan_dari_nol() -> None:
    # Laporan pertama di 5:00. 10 menit audio berikutnya selesai dalam 200 dtk
    # (3x realtime): sisa 25 menit audio ≈ 8 menit, bukan dihitung dari 0:00.
    _, message = transcribe_progress(900.0, 2400.0, elapsed_s=200.0, measured_from_s=300.0)
    assert message.endswith("±8 menit lagi")


def test_sisa_waktu_menunggu_cukup_audio_sejak_patokan() -> None:
    _, message = transcribe_progress(330.0, 2400.0, elapsed_s=10.0, measured_from_s=300.0)
    assert "lagi" not in message


def test_sisa_waktu_disembunyikan_sebelum_cukup_audio() -> None:
    _, message = transcribe_progress(ETA_MIN_AUDIO_S - 1, 2400.0, elapsed_s=5.0)
    assert "lagi" not in message


@pytest.mark.parametrize(("remaining_audio", "expected"), [(20.0, "<1 menit lagi"), (0.0, "")])
def test_sisa_waktu_di_ujung(remaining_audio: float, expected: str) -> None:
    total = 600.0
    _, message = transcribe_progress(total - remaining_audio, total, elapsed_s=200.0)
    assert message.endswith(expected)
    if not expected:
        assert "lagi" not in message


def test_format_jam_untuk_video_panjang() -> None:
    _, message = transcribe_progress(3725.0, 5400.0, elapsed_s=1000.0)
    assert message.startswith("Transkripsi 1:02:05 / 1:30:00")
