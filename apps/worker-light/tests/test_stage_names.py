"""Test konsistensi nama stage job.

**Mengapa test ini ada.** Nama stage adalah string yang dipakai di tiga tempat
yang tidak saling memeriksa: worker saat menulis, API saat menyaring, dan UI saat
menampilkan. Dua penyimpangan sudah benar-benar terjadi:

* ``"transcribing"`` ditulis worker, sedangkan ``"transcribe"`` dipakai di
  tempat lain — dua nama untuk satu tahap, sehingga filter berdasarkan stage
  tidak pernah cocok.
* Logika catatan di API membandingkan ``"ingesting"`` terhadap ``"ingest"``
  (kini API memakai status, bukan stage).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

#: Tahap yang sah. Sengaja berupa kata kerja dasar tanpa akhiran -ing, karena
#: itulah bentuk yang sudah dipakai basis data.
VALID_STAGES = {"upload", "ingest", "transcribe", "analyze", "render", "done"}


def _source_files() -> Iterator[Path]:
    """Berkas yang menulis atau membandingkan nama stage."""
    yield from (REPO / "apps" / "worker-light" / "worker_light").rglob("*.py")
    yield from (REPO / "apps" / "worker-render" / "worker_render").rglob("*.py")


class TestStageNames:
    """Nama stage yang ditulis worker harus ada di daftar sah."""

    def test_tidak_ada_nama_stage_dengan_akhiran_ing(self) -> None:
        """Bentuk -ing adalah kekeliruan yang pernah terjadi; tolak sejak awal."""
        offenders: list[str] = []

        for path in _source_files():
            text = path.read_text(encoding="utf-8")
            # Cari argumen posisi ketiga pada _emit(...) yang berupa string
            # literal — di situlah nama stage ditulis.
            for match in re.finditer(r'emit\((.*?)\)', text, re.DOTALL):
                block = match.group(1)
                literals = re.findall(r'"([a-z_]+)"', block)
                for literal in literals:
                    if literal in {"running", "done", "failed", "pending"}:
                        continue
                    if literal in VALID_STAGES:
                        continue
                    if literal.endswith("ing"):
                        offenders.append(f"{path.name}: {literal!r}")

        assert not offenders, (
            "nama stage memakai akhiran -ing, sedangkan nilai di basis data "
            "memakai bentuk dasar: " + ", ".join(offenders)
        )

    def test_stage_yang_ditulis_terdaftar(self) -> None:
        """Setiap nama stage yang ditulis harus termasuk daftar sah."""
        unknown: list[str] = []
        for path in _source_files():
            text = path.read_text(encoding="utf-8")
            # Nilai yang diteruskan ke _emit sebagai argumen stage.
            for match in re.finditer(r'emit\(\s*\w+,\s*"[a-z]+",\s*"([a-z_]+)"', text):
                stage = match.group(1)
                if stage not in VALID_STAGES:
                    unknown.append(f"{path.name}: {stage!r}")

        assert not unknown, f"stage di luar daftar sah: {unknown}"
