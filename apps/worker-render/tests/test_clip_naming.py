"""Test penamaan berkas klip hasil render.

Hasil render ditulis ke folder job yang dibaca manusia
(``output/<slug-judul>-<id>``), jadi namanya harus deskriptif — bukan
``preview.mp4`` yang tidak memberi tahu video asal, urutan, maupun judul.
Fungsi :func:`_describe_clip` yang menyusunnya, dan test ini mengunci sifat
penting untuk nama berkas:

* aman dipakai di Windows **dan** Linux (tanpa karakter terlarang);
* tidak pernah kosong, walau label segmen kosong atau hanya berisi simbol;
* memuat ID job dan segmen sehingga hasil bisa ditelusuri balik ke database;
* panjangnya wajar (bukan ratusan karakter).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))

#: Karakter yang tidak sah di nama berkas Windows.
INVALID_CHARS = set('<>:"/\\|?*')

#: Nama perangkat lama Windows yang tidak boleh dipakai sebagai nama berkas.
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10))}


class TestDescribeClip:
    """Bentuk nama berkas hasil render."""

    def test_memuat_id_job_dan_segmen(self) -> None:
        """ID harus ada agar berkas bisa ditelusuri balik ke baris database."""
        from worker_render.tasks import _describe_clip

        name = _describe_clip(
            job_id="4d6d1fb7-9a1a-4b61-bc25-3f272e971293",
            segment_id="ec596c52-3ede-4f7d-9718-d7ea1650f2ae",
            label="Mongol Bongkar Kejanggalan",
            kind="preview",
        )
        assert name.startswith("4d6d1fb7_ec596c52_")
        assert name.endswith("_preview.mp4")

    def test_label_menjadi_slug(self) -> None:
        """Label dijadikan slug: huruf kecil, hanya ``a-z0-9-``."""
        from worker_render.tasks import _describe_clip

        name = _describe_clip(
            job_id="a", segment_id="b",
            label="Pengakuan Mongol Soal Ritual Pengorbanan Satanik!",
            kind="preview",
        )
        assert "pengakuan-mongol-soal-ritual-pengorbanan" in name

    @pytest.mark.parametrize(
        "label",
        [
            "",
            "   ",
            "!!!",
            "///",
            "Ünïcödé & Simbol",
            "a" * 300,
            "nama.dengan.titik",
        ],
    )
    def test_label_aneh_tetap_menghasilkan_nama_aman(self, label: str) -> None:
        """Label apa pun tidak boleh menghasilkan nama berkas yang tidak sah.

        Label berasal dari keluaran LLM, jadi isinya tidak dapat dipercaya —
        bisa kosong, penuh simbol, atau sangat panjang.
        """
        from worker_render.tasks import _describe_clip

        name = _describe_clip(job_id="a", segment_id="b", label=label, kind="preview")

        assert not (set(name) & INVALID_CHARS), f"karakter tidak sah di {name!r}"
        assert ".." not in name, f"nama memuat '..': {name!r}"
        assert name, "nama kosong"
        assert len(name) <= 120, f"nama kepanjangan ({len(name)}): {name!r}"
        # Bagian sebelum ekstensi tidak boleh kosong (mis. '_preview.mp4').
        stem = name.rsplit("_", 1)[0]
        assert stem.strip("_"), f"batang nama kosong: {name!r}"

    def test_tidak_berakhir_titik_atau_spasi(self) -> None:
        """Windows menolak nama berkas yang berakhir titik atau spasi."""
        from worker_render.tasks import _describe_clip

        for label in ("akhir.", "akhir ", "...", "a. "):
            name = _describe_clip(job_id="a", segment_id="b", label=label, kind="preview")
            assert not name.rstrip(".mp4").endswith((".", " ")), name

    def test_bukan_nama_perangkat_windows(self) -> None:
        """``CON.mp4`` dan sejenisnya tidak dapat dibuat di Windows."""
        from worker_render.tasks import _describe_clip

        name = _describe_clip(job_id="a", segment_id="b", label="CON", kind="preview")
        # Nama selalu berawalan ID, jadi tidak akan pernah sama dengan nama
        # perangkat — test ini menjaga sifat itu bila format berubah.
        assert Path(name).stem.upper() not in RESERVED

    def test_jenis_ikut_tercetak(self) -> None:
        """``preview`` dan ``final`` harus dapat dibedakan dari namanya."""
        from worker_render.tasks import _describe_clip

        def describe(kind: str) -> str:
            return _describe_clip(job_id="a", segment_id="b", label="x", kind=kind)

        assert describe("preview").endswith("_preview.mp4")
        assert describe("final").endswith("_final.mp4")

    def test_hanya_karakter_portabel(self) -> None:
        """Nama harus aman di Linux (case-sensitive) maupun Windows."""
        from worker_render.tasks import _describe_clip

        name = _describe_clip(
            job_id="a", segment_id="b", label="Label Dengan SPASI & Simbol #1",
            kind="preview",
        )
        assert re.fullmatch(r"[a-z0-9._-]+", name), f"karakter non-portabel: {name!r}"
