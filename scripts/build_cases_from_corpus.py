"""Bangun kasus eval dari korpus klip yang sudah dikumpulkan.

Berbeda dari ``bootstrap_eval_cases.py`` yang mengambil transkrip dari job yang
sudah diproses, skrip ini memakai korpus klip viral. Dua keunggulannya:

* **Timing asli.** Transkrip diambil dari subtitle YouTube (format ``json3``
  yang menyimpan ``tStartMs``), bukan persamaan rata seperti saat kasus
  viral yang lama dibuat. Perbedaan ini penting: perkiraan membuat IoU
  meleset dan skor terlihat jauh lebih buruk daripada kenyataannya.
* **Label tunggal yang tidak ambigu.** Karena kasus ini hanya klip pendek yang
  seluruhnya adalah satu momen, labelnya ``0`` sampai akhir transkrip. Tidak ada
  keputusan "momen mana di dalam video ini" yang perlu ditebak.

Kasus yang transkripnya terlalu pendek atau tidak punya timing dilewati dan
dilaporkan, bukan diisi dengan tebakan.

Pemakaian::

    .venv-win\\Scripts\\python.exe scripts\\build_cases_from_corpus.py --corpus <dir> --out <dir> --dry-run
    .venv-win\\Scripts\\python.exe scripts\\build_cases_from_corpus.py --corpus <dir> --out <dir>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

#: Akar repo: ``<repo>/scripts/build_cases_from_corpus.py``.
REPO_ROOT = Path(__file__).resolve().parents[1]

for _entry in ("apps/worker-render", "packages/shared/src", "apps/worker-light", "apps/api"):
    _path = str(REPO_ROOT / _entry)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fix_corpus_timestamps import (  # noqa: E402
    REAL_TIMING,
    fetch_timed_transcript,
    render_lines,
)

logger = logging.getLogger("build-cases")

#: Transkrip lebih pendek dari ini tidak bisa dinilai — kasusnya hanya mengukur
#: tebakan, bukan kualitas pemilihan momen.
MIN_CHARS = 150

#: Jeda antar permintaan subtitle, detik.
DELAY_S = 4.0


def build(
    *,
    corpus: Path,
    out: Path,
    dry_run: bool,
    cookies: str | None,
) -> int:
    """Bangun kasus dari setiap klip dalam korpus.

    Args:
        corpus: Direktori korpus klip.
        out: Direktori keluaran kasus.
        dry_run: Bila ``True``, hanya laporkan tanpa menulis.
        cookies: Berkas cookies Netscape.

    Returns:
        0 bila berhasil menulis, 1 bila ada yang gagal.
    """
    if not corpus.is_dir():
        print(f"Direktori korpus tidak ada: {corpus}", file=sys.stderr)
        return 2

    sources = sorted(corpus.glob("*.json"))
    if not sources:
        print(f"Tidak ada klip di {corpus}", file=sys.stderr)
        return 2

    print(f"Klip di korpus : {len(sources)}")
    if dry_run:
        for path in sources:
            print(f"  {path.name}")
        return 0

    out.mkdir(parents=True, exist_ok=True)
    written = skipped = 0

    for index, path in enumerate(sources):
        try:
            clip = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Lewati %s: %s", path.name, exc)
            skipped += 1
            continue

        video_id = str(clip.get("source_video_id") or "")
        if not video_id:
            logger.warning("Lewati %s: source_video_id kosong.", path.name)
            skipped += 1
            continue

        if index:
            time.sleep(DELAY_S)

        rows = fetch_timed_transcript(video_id, cookies=cookies)
        if not rows:
            print(f"  lewati {path.name}: subtitle tidak tersedia")
            skipped += 1
            continue

        transcript = render_lines(rows)
        if len(transcript) < MIN_CHARS:
            print(f"  lewati {path.name}: transkrip cuma {len(transcript)} karakter")
            skipped += 1
            continue

        last_s = rows[-1][0]
        slug = str(clip.get("slug") or path.stem)

        payload = {
            "slug": slug,
            "video_duration_s": float(clip.get("duration_s") or 0.0),
            "target_count": 1,
            "source_video_id": video_id,
            "source_view_count": clip.get("view_count"),
            "source_channel": clip.get("channel"),
            "transcript": transcript,
            "transcript_source": REAL_TIMING,
            "timed_segments": len(rows),
            # Seluruh klip pendek adalah satu momen; tidak ada keputusan
            # "bagian mana yang terbaik" yang perlu ditebak saat pelabelan.
            "expected": [
                {
                    "start_s": 0.0,
                    "end_s": round(last_s, 1),
                    "note": "Seluruh klip: video pendek yang berdiri sendiri.",
                }
            ],
        }

        target = out / f"{slug}.json"
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"  tulis {target.name} ({len(transcript):,} karakter, {last_s:.0f}s)")
        written += 1

    print()
    print(f"Ditulis : {written}")
    print(f"Dilewati: {skipped}")
    if written:
        print()
        print("Jalankan evaluasi dengan:")
        print("  .venv-win\\Scripts\\python.exe scripts\\eval_scoring.py")
    return 0 if not skipped else 1


def main(argv: list[str] | None = None) -> int:
    """Titik masuk CLI.

    Args:
        argv: Argumen baris perintah; ``None`` berarti memakai ``sys.argv``.

    Returns:
        Kode keluar proses.
    """
    parser = argparse.ArgumentParser(
        description="Bangun kasus eval dari korpus klip viral."
    )
    parser.add_argument(
        "--corpus", type=Path, default=REPO_ROOT / "eval" / "viral" / "clips", help="Korpus klip."
    )
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "eval" / "scoring" / "cases", help="Keluaran kasus."
    )
    parser.add_argument("--dry-run", action="store_true", help="Hanya laporkan.")
    parser.add_argument("--cookies", help="Berkas cookies Netscape.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    return build(corpus=args.corpus, out=args.out, dry_run=args.dry_run, cookies=args.cookies)


if __name__ == "__main__":
    raise SystemExit(main())