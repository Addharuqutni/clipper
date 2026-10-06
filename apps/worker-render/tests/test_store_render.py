"""Test penyimpanan hasil render: satu job, satu folder.

Video sumber dan klip hasil render harus berada di folder job yang sama
(``output/<slug-judul>-<id>``), sehingga tidak ada salinan ganda dan pengguna
menemukan semuanya di satu tempat. Folder ditentukan oleh ``r2_key`` media
sumber yang sudah tersimpan, bukan dihitung ulang dari judul.
"""

from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))

#: Skema minimal: hanya kolom yang dibaca ``_job_dir`` dan ``_source_path``.
_SCHEMA = """
CREATE TABLE source_media (
    id TEXT PRIMARY KEY, job_id TEXT UNIQUE, r2_key TEXT,
    size_bytes INTEGER, duration_s REAL, width INTEGER, height INTEGER,
    codec TEXT, language TEXT, transcript_source TEXT, created_at TEXT
)
"""

@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Arahkan penyimpanan ke direktori sementara, tanpa basis data."""
    monkeypatch.setenv("LOCAL_STORAGE_DIR", str(tmp_path / "store"))
    return tmp_path

def _db(tmp_path: Path, rows: list[tuple[str, str]]) -> None:
    """SQLite berisi satu baris ``source_media`` per ``(job_id, r2_key)``.

    Berkas media sumbernya juga dibuat di jalur yang ditunjuk key, supaya test
    memeriksa keadaan yang sebenarnya ada di disk.
    """
    path = tmp_path / "store" / "clipper.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(_SCHEMA)
        connection.executemany(
            "INSERT INTO source_media (id, job_id, r2_key) VALUES (?, ?, ?)",
            [(f"media-{job_id}", job_id, key) for job_id, key in rows],
        )
        connection.commit()
    for _job_id, key in rows:
        media = tmp_path / "store" / key
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_bytes(b"sumber")

def _rendered(tmp_path: Path, name: str = "hasil.mp4") -> Path:
    source = tmp_path / name
    source.write_bytes(b"video-bytes" * 100)
    return source

JOB_ONE = "4d6d1fb7-9a1a-4b61-bc25-3f272e971293"
JOB_TWO = "8c1a2e4f-6b7d-4e90-a123-456789abcdef"

def test_klip_ditaruh_di_folder_job_yang_sama_dengan_video_sumber(storage: Path) -> None:
    """Inti tata letak: hasil render berkumpul dengan videonya, tanpa salinan."""
    from worker_render.tasks import _store_render

    _db(storage, [(JOB_ONE, f"podcast-barat-{JOB_ONE[:8]}/source.mp4")])

    key = _store_render("seg-1", "final", _rendered(storage), job_id=JOB_ONE, label="Judul Segmen")

    folder = storage / "store" / f"podcast-barat-{JOB_ONE[:8]}"
    assert key == f"podcast-barat-{JOB_ONE[:8]}/{JOB_ONE[:8]}_seg-1_01_judul-segmen_final.mp4"
    assert (storage / "store" / key).is_file()
    assert {p.name for p in folder.iterdir()} == {"source.mp4", Path(key).name}

def test_render_ulang_segmen_yang_sama_memakai_urutan_berikutnya(storage: Path) -> None:
    """Render ulang tidak boleh menimpa klip lama yang mungkin sudah dipakai."""
    from worker_render.tasks import _store_render

    _db(storage, [(JOB_ONE, f"podcast-{JOB_ONE[:8]}/source.mp4")])

    first = _store_render("seg-1", "final", _rendered(storage, "satu.mp4"), job_id=JOB_ONE, label="Judul")
    second = _store_render("seg-1", "final", _rendered(storage, "dua.mp4"), job_id=JOB_ONE, label="Judul")

    assert first != second
    assert (storage / "store" / first).is_file()
    assert (storage / "store" / second).is_file()

def test_media_tata_letak_lama_memakai_folder_id_job(storage: Path) -> None:
    """Baris yang belum dimigrasi tetap dapat dirender, di folder ``<job_id>``."""
    from worker_render.tasks import _store_render

    _db(storage, [(JOB_ONE, f"raw/{JOB_ONE}/source.mp4")])

    key = _store_render("seg-1", "final", _rendered(storage), job_id=JOB_ONE, label="Judul")

    assert Path(key).parent.name == JOB_ONE

def test_job_tanpa_media_ditolak(storage: Path) -> None:
    """Tanpa baris ``source_media``, folder job tidak dapat ditentukan.

    Render tidak mungkin tanpa video sumber, jadi ini keadaan rusak — bukan
    job baru yang belum selesai diunggah.
    """
    from clipper_shared.job_media import job_dir

    _db(storage, [])

    with pytest.raises(RuntimeError, match="belum punya media sumber"):
        job_dir(JOB_TWO)

def test_nama_folder_dari_basis_data_tidak_boleh_keluar_akar(storage: Path) -> None:
    """``r2_key`` berasal dari basis data, jadi tetap divalidasi saat dipakai."""
    from clipper_shared import storage as layout

    with pytest.raises(ValueError, match="Nama folder job tidak valid"):
        layout.job_dir("../../etc")
