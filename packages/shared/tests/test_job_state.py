"""Kosakata dan skala progres job — satu tempat untuk semuanya.

Sebelum :mod:`clipper_shared.job_state`, pengetahuan siklus hidup job tersebar:
``ACTIVE_STATUSES`` didefinisikan ulang di service API, skala progres ditulis
sebagai angka mentah di modul worker (``TRANSCRIBE_START = 20``, ``75 + int(25*n/t)``),
dan keputusan status akhir job tinggal di dalam modul render.

Test ini mengunci tiga sifat yang membuat modul itu berguna:

* **Pita progres berurutan dan tidak tumpang tindih.** Kalau tidak, bilah
  progres bisa mundur saat pipeline berpindah tahap — bug yang pernah terjadi
  (ingest berakhir di 70, transkripsi melompat mundur ke 25).
* **Predikat status sesuai dengan CHECK constraint basis data.** Job yang
  dianggap "aktif" tetapi tidak ada di daftar status akan membuat tombol
  "Proses ulang" menolak job yang sebenarnya bisa dikirim ulang.
* **Nasib job dari hasil render.** Satu klip gagal tidak boleh menandai seluruh
  job gagal — sembilan klip bagus ikut tampil sebagai kegagalan.
"""

from __future__ import annotations

import re

import pytest
from clipper_shared.job_state import (
    ACTIVE_STATUSES,
    DONE,
    JOB_STAGES,
    JOB_STATUSES,
    TERMINAL_STATUSES,
    RenderSummary,
    is_active,
    is_terminal,
    progress,
    render_summary,
)


class TestVocabulary:
    def test_status_aktif_dan_terminal_menutupi_semua_status(self) -> None:
        """Setiap status harus jelas: sedang berjalan, atau sudah berakhir."""
        assert set(JOB_STATUSES) == ACTIVE_STATUSES | TERMINAL_STATUSES
        assert not (ACTIVE_STATUSES & TERMINAL_STATUSES)

    def test_status_cocok_dengan_check_constraint_basis_database(self) -> None:
        """Daftar di Python harus sama dengan yang benar-benar diterima SQLite."""
        from app.models.job import Job

        constraint = next(
            c for c in Job.__table__.constraints if (getattr(c, "name", "") or "").endswith("_status_valid")
        )
        assert set(JOB_STATUSES) == set(re.findall(r"'([a-z]+)'", str(constraint.sqltext)))

    def test_tahap_upload_di_awal_dan_done_di_akhir(self) -> None:
        assert JOB_STAGES[0] == "upload"
        assert JOB_STAGES[-1] == "done"

    @pytest.mark.parametrize("status", JOB_STATUSES)
    def test_predikat_konsisten_dengan_daftarnya(self, status: str) -> None:
        assert is_active(status) is (status in ACTIVE_STATUSES)
        assert is_terminal(status) is (status in TERMINAL_STATUSES)

    def test_status_tidak_dikenal_bukan_aktif_maupun_terminal(self) -> None:
        """Nilai asing (mis. baris rusak) tidak boleh dianggap sedang berjalan."""
        assert not is_active("entah")
        assert not is_terminal("entah")
        assert not is_active(None)

class TestProgressScale:
    def test_pita_berurutan_tanpa_tumpang_tindih(self) -> None:
        """Bilah progres tidak boleh mundur saat pipeline berpindah tahap."""
        stages = [stage for stage in JOB_STAGES if stage != "upload"]
        tops = [progress(stage, 1.0) for stage in stages]
        bottoms = [progress(stage, 0.0) for stage in stages]

        assert bottoms == sorted(bottoms)
        assert tops == sorted(tops)
        # Awal sebuah tahap tidak pernah di bawah akhir tahap sebelumnya.
        assert all(bottom >= top for bottom, top in zip(bottoms[1:], tops, strict=False))

    def test_selalu_dalam_rentang_nol_sampai_seratus(self) -> None:
        for stage in JOB_STAGES:
            for fraction in (-1.0, 0.0, 0.5, 1.0, 2.0):
                value = progress(stage, fraction)
                assert 0 <= value <= 100, f"{stage} @ {fraction} = {value}"

    def test_pecahan_di_luar_rentang_dijepit(self) -> None:
        """Posisi audio bisa melewati durasi terlapor; progres tetap di ujung."""
        assert progress("transcribe", -0.5) == progress("transcribe", 0.0)
        assert progress("transcribe", 1.5) == progress("transcribe", 1.0)

    def test_naik_monoton_di_dalam_satu_tahap(self) -> None:
        values = [progress("transcribe", step / 20) for step in range(21)]
        assert values == sorted(values)

    def test_tahap_selesai_selalu_seratus(self) -> None:
        assert progress("done") == 100

    def test_tahap_tidak_dikenal_ditolak(self) -> None:
        """Tahap berasal dari kode; nilai asing adalah bug yang harus terlihat."""
        with pytest.raises(ValueError, match="Tahap tidak dikenal"):
            progress("tahap-karangan")

class TestRenderSummary:
    @pytest.mark.parametrize(
        ("statuses", "expected"),
        [
            (["done", "canceled", "canceled"], (DONE, "1 klip selesai.")),
            (["done", "failed", "canceled"], (DONE, "1 klip selesai, 1 gagal (bisa dirender ulang).")),
            # Satu render ulang gagal, sisanya dibatalkan: gagal dihitung dari 1, bukan 0.
            (["failed", "canceled", "canceled"], ("failed", "Semua 1 render gagal. Lihat log untuk detail.")),
            (["canceled", "canceled"], (DONE, "Tidak ada klip yang dirender (semua render dibatalkan).")),
        ],
    )
    def test_render_canceled_bukan_hasil_maupun_kegagalan(
        self, statuses: list[str], expected: tuple[str, str]
    ) -> None:
        summary = render_summary(statuses)

        assert summary.pending == 0
        assert summary.final_outcome() == expected

    def test_progres_tidak_memasukkan_render_yang_dibatalkan(self) -> None:
        summary = render_summary(["running", "done", "canceled", "canceled"])

        assert (summary.pending, summary.finished, summary.total) == (1, 1, 2)

    def test_pecahan_untuk_progres_tahap_render(self) -> None:
        assert render_summary(["done", "done"]).fraction == 1.0
        assert render_summary(["running", "done"]).fraction == 0.5

    def test_tanpa_render_tidak_membagi_nol(self) -> None:
        summary = RenderSummary(pending=0, done=0, failed=0)

        assert summary.fraction == 0.0
        assert summary.final_outcome()[0] == DONE
