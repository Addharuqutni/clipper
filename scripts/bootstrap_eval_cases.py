"""Buat draf kasus eval dari job yang sudah diproses.

Melabeli momen adalah pekerjaan manual yang tidak bisa digantikan skrip. Yang
bisa digantikan adalah **menyiapkan** bahan: mengambil transkrip yang sudah ada
dan merender-ya persis dalam format yang dikirim ke model, lalu meninggalkan
daftar ``expected`` kosong untuk diisi manusia.

Skrip ini **hanya membaca** basis data; tidak menulis apa pun ke
``output/clipper.db`` dan tidak menyentuh media pengguna.

Pemakaian::

    .venv-win\\Scripts\\python.exe scripts\\bootstrap_eval_cases.py
    .venv-win\\Scripts\\python.exe scripts\\bootstrap_eval_cases.py --job afe90fc9
    .venv-win\\Scripts\\python.exe scripts\\bootstrap_eval_cases.py --max-minutes 30
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

#: Akar repo: ``<repo>/scripts/bootstrap_eval_cases.py``.
REPO_ROOT = Path(__file__).resolve().parents[1]

for _entry in ("apps/worker-render", "packages/shared/src", "apps/worker-light", "apps/api"):
    _path = str(REPO_ROOT / _entry)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from worker_light.scoring_client import _render_transcript  # noqa: E402

#: Basis data bawaan.
DEFAULT_DB = REPO_ROOT / "output" / "clipper.db"

#: Direktori keluaran draf kasus.
DEFAULT_OUT = REPO_ROOT / "eval" / "scoring" / "cases"

#: Bucket transkrip, detik — harus sama dengan yang dipakai produksi.
BUCKET_S = 12.0


def _slugify(title: str, job_id: str) -> str:
    """Ubah judul video menjadi nama berkas yang aman.

    Args:
        title: Judul video.
        job_id: Pengenal job, dipakai bila judul tidak berguna.

    Returns:
        Slug yang aman dipakai sebagai nama berkas.
    """
    cleaned = "".join(
        char if char.isalnum() else "-" for char in (title or "").lower()
    ).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return (cleaned[:40] or f"job-{job_id[:8]}")


def _render_words(words: list[dict[str, object]]) -> str:
    """Render daftar kata menjadi transkrip bertimestamp.

    Memakai fungsi yang sama dengan produksi bila tersedia, supaya draf kasus
    benar-benar identik dengan yang dikirim ke model.

    Args:
        words: Daftar kata bertimestamp.

    Returns:
        Teks transkrip berformat baris ``[12.4s] teks ...``.
    """
    return _render_transcript(words)


def _duration_s(words: list[dict[str, object]]) -> float:
    """Ambil durasi video dari kata terakhir.

    Args:
        words: Daftar kata bertimestamp.

    Returns:
        Detik; 0 bila kosong.
    """
    return max((float(word.get("end_s") or 0.0) for word in words), default=0.0)


def _target_count(duration_s: float) -> int:
    """Jumlah klip yang wajar untuk durasi ini.

    Mengikuti aturan produksi: satu klip per 3 menit, dibatasi 1..30.

    Args:
        duration_s: Durasi video dalam detik.

    Returns:
        Jumlah target klip.
    """
    from clipper_shared.scoring import MAX_SEGMENTS, MIN_SEGMENTS, MINUTES_PER_CLIP

    ceiling = int(duration_s // (MINUTES_PER_CLIP * 60))
    return max(MIN_SEGMENTS, min(MAX_SEGMENTS, ceiling))


def bootstrap(*, db: Path, out: Path, job: str | None, max_minutes: float) -> int:
    """Buat draf kasus dari satu atau semua job.

    Args:
        db: Berkas SQLite.
        out: Direktori keluaran.
        job: Awalan id job tertentu; ``None`` berarti semua.
        max_minutes: Lewati job yang durasinya melebihi ini.

    Returns:
        Kode keluar proses.
    """
    if not db.is_file():
        print(f"Basis data tidak ditemukan: {db}", file=sys.stderr)
        return 2

    out.mkdir(parents=True, exist_ok=True)
    # Mode baca-saja: skrip ini tidak boleh mengubah basis data pengguna.
    connection = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row

    try:
        rows = connection.execute(
            """
            SELECT j.id AS job_id, j.video_title AS title, t.words AS words
            FROM jobs j
            JOIN transcripts t ON t.job_id = j.id
            WHERE t.words IS NOT NULL AND t.words != ''
            ORDER BY j.created_at DESC
            """
        ).fetchall()
    except sqlite3.OperationalError as exc:
        print(f"Gagal membaca basis data: {exc}", file=sys.stderr)
        return 2

    written = 0
    skipped = 0
    for row in rows:
        job_id = str(row["job_id"])
        if job and not job_id.startswith(job):
            continue

        try:
            words = json.loads(row["words"])
        except json.JSONDecodeError:
            print(f"  lewati {job_id[:8]}: kolom words bukan JSON valid.", file=sys.stderr)
            skipped += 1
            continue

        if not isinstance(words, list) or not words:
            print(f"  lewati {job_id[:8]}: transkrip kosong.")
            skipped += 1
            continue

        duration_s = _duration_s(words)
        if max_minutes and duration_s > max_minutes * 60:
            print(
                f"  lewati {job_id[:8]}: {duration_s / 60:.0f} menit melebihi batas "
                f"{max_minutes:.0f} menit.",
            )
            skipped += 1
            continue

        transcript = _render_words(words)
        slug = _slugify(str(row["title"] or ""), job_id)
        payload = {
            "slug": slug,
            "video_duration_s": round(duration_s, 1),
            "target_count": _target_count(duration_s),
            "source_job_id": job_id,
            "transcript": transcript,
            "expected": [],
        }

        path = out / f"{slug}.json"
        if path.exists():
            print(f"  sudah ada, dilewati: {path.name}")
            skipped += 1
            continue

        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"  tulis {path.name}  ({duration_s / 60:.0f} menit, "
            f"{len(transcript):,} karakter transkrip, {payload['target_count']} target klip)"
        )
        written += 1

    connection.close()

    print()
    print(f"Draf ditulis : {written}")
    print(f"Dilewati     : {skipped}")
    if written:
        print()
        print("Draf ini BELUM bisa dipakai: isi dulu daftar 'expected' di tiap berkas.")
        print("Tonton videonya, tandai momen yang layak diklip, baru jalankan:")
        print("  .venv-win\\Scripts\\python.exe scripts\\eval_scoring.py")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Titik masuk CLI.

    Args:
        argv: Argumen baris perintah; ``None`` berarti memakai ``sys.argv``.

    Returns:
        Kode keluar proses.
    """
    parser = argparse.ArgumentParser(
        description="Buat draf kasus eval dari job yang sudah diproses."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Berkas SQLite.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Direktori keluaran.")
    parser.add_argument("--job", help="Awalan id job tertentu.")
    parser.add_argument(
        "--max-minutes", type=float, default=60.0, help="Lewati job lebih panjang dari ini."
    )
    args = parser.parse_args(argv)
    return bootstrap(db=args.db, out=args.out, job=args.job, max_minutes=args.max_minutes)


if __name__ == "__main__":
    raise SystemExit(main())
