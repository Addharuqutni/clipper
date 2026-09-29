"""Test status akhir job dari render terbaru tiap segmen.

Render ``canceled`` dihentikan pengguna: bukan hasil dan bukan kegagalan. Job
yang sebagian segmennya dibatalkan lalu satu segmen dirender ulang harus
dilaporkan menurut render yang benar-benar dijalankan saja.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["done", "canceled", "canceled"], ("done", "1 klip selesai.")),
        (["done", "failed", "canceled"], ("done", "1 klip selesai, 1 gagal (bisa dirender ulang).")),
        # Satu render ulang gagal, sisanya dibatalkan: gagal dihitung dari 1, bukan 0.
        (["failed", "canceled", "canceled"], ("failed", "Semua 1 render gagal. Lihat log untuk detail.")),
        (["canceled", "canceled"], ("done", "Tidak ada klip yang dirender (semua render dibatalkan).")),
    ],
)
def test_render_canceled_bukan_hasil_maupun_kegagalan(statuses: list[str], expected: tuple[str, str]) -> None:
    from worker_render.tasks import _render_summary

    summary = _render_summary(statuses)

    assert summary.pending == 0
    assert summary.final_outcome() == expected


def test_progres_tidak_memasukkan_render_yang_dibatalkan() -> None:
    from worker_render.tasks import _render_summary

    summary = _render_summary(["running", "done", "canceled", "canceled"])

    assert (summary.pending, summary.finished, summary.total) == (1, 1, 2)
