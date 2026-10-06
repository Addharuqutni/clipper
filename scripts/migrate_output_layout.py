"""Pindahkan media job lama ke tata letak satu-folder-per-job.

Versi sebelumnya menyebar media ke ``output/clipper-raw/raw/<job_id>/`` dan
``output/clipper-renders/renders/<segment_id>/``, sementara salinan bernama
untuk pengguna ada di ``output/clips/<job_id>/``. Tata letak baru menaruh
semuanya di ``output/<slug-judul>-<id>/`` dan mencatat key relatif terhadap akar
penyimpanan di kolom ``r2_key``.

Karena skrip ini mengubah **berkas dan basis data sekaligus**:

* **bawaan = dry-run.** Tidak ada yang ditulis; hanya rencana yang dicetak.
* ``--apply`` menyalin berkas lebih dulu, baru memperbarui ``r2_key``, dan
  menyisakan berkas lama supaya masih bisa diperiksa. Setelah yakin, hapus
  sisanya dengan ``--clean``.
* Backup ``clipper.db`` dibuat sebelum basis data diubah.
* ``clipper-overlays`` dan ``clipper-fonts`` tidak disentuh: keduanya aset
  bersama antar job, bukan media job.

Pemakaian::

    .venv-win\\Scripts\\python.exe scripts\\migrate_output_layout.py
    .venv-win\\Scripts\\python.exe scripts\\migrate_output_layout.py --apply
    .venv-win\\Scripts\\python.exe scripts\\migrate_output_layout.py --clean
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in ("packages/shared/src",):
    _path = str(REPO_ROOT / _entry)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from clipper_shared import storage as layout  # noqa: E402

logger = logging.getLogger("migrate_output_layout")

def _plan(db: Path) -> tuple[list[tuple[Path, Path]], list[tuple[str, str, str, str]]]:
    """Rencana dari basis data: pasangan berkas, dan baris ``r2_key``.

    Nama folder dihitung dengan fungsi yang sama seperti kode produksi
    (:func:`clipper_shared.storage.job_folder_name`), jadi hasil migrasi identik
    dengan job yang dibuat setelah perubahan ini.
    """
    moves: list[tuple[Path, Path]] = []
    rows: list[tuple[str, str, str, str]] = []
    with sqlite3.connect(db) as connection:
        for job_id, title, key in connection.execute(
            "SELECT s.job_id, j.video_title, s.r2_key FROM source_media s JOIN jobs j ON j.id = s.job_id"
        ):
            if not key or not layout.is_legacy_key(str(key)):
                continue
            folder = layout.job_folder_name(str(job_id), title)
            new_key = f"{folder}/{layout.safe_filename(str(key))}"
            moves.append((layout.key_path(str(key)), layout.job_dir(folder) / Path(new_key).name))
            rows.append(("source_media", str(job_id), str(key), new_key))

        for segment_id, label, kind, key in connection.execute(
            "SELECT s.id, s.label, r.kind, r.r2_key "
            "FROM renders r JOIN segments s ON s.id = r.segment_id"
        ):
            if not key or not layout.is_legacy_key(str(key)):
                continue
            job_id, title = connection.execute(
                "SELECT s.job_id, j.video_title FROM segments s "
                "JOIN jobs j ON j.id = s.job_id WHERE s.id = ?",
                (segment_id,),
            ).fetchone()
            folder = layout.job_folder_name(str(job_id), title)
            name = (
                f"{str(job_id)[:8]}_{str(segment_id)[:8]}_01_"
                f"{layout.slugify(str(label or ''), limit=40) or 'segmen'}_{kind}.mp4"
            )
            moves.append((layout.key_path(str(key)), layout.job_dir(folder) / name))
            rows.append(("renders", str(segment_id), str(key), f"{folder}/{name}"))
    return moves, rows

def _apply(db: Path, moves: list[tuple[Path, Path]], rows: list[tuple[str, str, str, str]]) -> None:
    backup = db.with_name(db.name + ".bak-layout")
    if not backup.exists():
        shutil.copy2(db, backup)
        logger.info("Backup basis data: %s", backup)

    copied = 0
    for source, target in moves:
        if not source.is_file() or target.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Hard link bila bisa: instan dan tidak memakan ruang, dan berkas
            # lama boleh dihapus kapan saja tanpa memutus yang baru.
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)
        copied += 1
    logger.info("%d berkas dipindahkan ke folder job.", copied)

    with sqlite3.connect(db) as connection:
        for table, row_id, old_key, new_key in rows:
            column = "job_id" if table == "source_media" else "segment_id"
            connection.execute(
                f"UPDATE {table} SET r2_key = ? WHERE {column} = ? AND r2_key = ?",  # noqa: S608
                (new_key, row_id, old_key),
            )
        connection.commit()
    logger.info("%d baris r2_key diperbarui.", len(rows))

def _clean() -> None:
    """Hapus folder tata letak lama. Hanya setelah ``--apply`` diverifikasi."""
    root = layout.storage_root()
    for name in (layout.RAW, layout.RENDERS, "clips"):
        path = root / name
        if path.is_dir():
            shutil.rmtree(path)
            logger.info("Dihapus: %s", path)

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="salin berkas dan perbarui basis data")
    parser.add_argument("--clean", action="store_true", help="hapus folder tata letak lama")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.clean:
        _clean()
        return 0

    from clipper_shared.db import get_sqlite_path

    db = get_sqlite_path()
    moves, rows = _plan(db)
    print(f"Rencana: {len(moves)} berkas, {len(rows)} baris r2_key.")
    for source, target in moves:
        print(f"  {source}  ->  {target.parent.name}/{target.name}")
    for table, _row_id, old_key, new_key in rows:
        print(f"  [{table}] {old_key}  ->  {new_key}")

    if not args.apply:
        print("\nDry-run. Jalankan ulang dengan --apply untuk mengeksekusi.")
        return 0

    _apply(db, moves, rows)
    print("\nSelesai. Periksa hasilnya, lalu hapus sisa folder lama dengan --clean.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
