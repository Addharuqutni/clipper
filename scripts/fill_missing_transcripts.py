"""Isi transkrip yang masih kosong di korpus viral.

Korpus bisa berisi berkas dengan ``transcript`` kosong — terjadi bila YouTube
mengembalikan HTTP 429 saat pengumpulan, atau bila metadata dikumpulkan tanpa
subtitle. Skrip ini melengkapinya **tanpa mengumpulkan ulang**: ia membaca
berkas yang sudah ada, mengambil transkripnya, lalu menulis balik.

Sengaja **idempoten**: berkas yang sudah punya transkrip dilewati, jadi aman
dijalankan berulang kali sampai semua terisi.

Pemakaian::

    .venv-win\\Scripts\\python.exe scripts\\fill_missing_transcripts.py
    .venv-win\\Scripts\\python.exe scripts\\fill_missing_transcripts.py --cookies <berkas>
    .venv-win\\Scripts\\python.exe scripts\\fill_missing_transcripts.py --dry-run

Kode keluar 0 bila semua terisi atau tidak ada yang perlu diisi; 1 bila masih
ada yang gagal (biasanya rate limit — jalankan lagi nanti).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

#: Akar repo: ``<repo>/scripts/fill_missing_transcripts.py``.
REPO_ROOT = Path(__file__).resolve().parents[1]

for _entry in ("apps/worker-render", "packages/shared/src", "apps/worker-light", "apps/api"):
    _path = str(REPO_ROOT / _entry)
    if _path not in sys.path:
        sys.path.insert(0, _path)

logger = logging.getLogger("fill")

#: Direktori korpus.
DEFAULT_CORPUS = REPO_ROOT / "eval" / "viral" / "clips"

#: Jeda antar klip, detik. Pengunduhan subtitle sensitif terhadap laju.
DELAY_S = 6.0

#: Bahasa yang dicoba berurutan.
LANGS = ("id", "id-ID", "en", "en-US")


def fill(*, corpus: Path, dry_run: bool, cookies: str | None) -> int:
    """Lengkapi transkrip yang kosong.

    Args:
        corpus: Direktori korpus.
        dry_run: Bila ``True``, hanya lapor tanpa mengambil apa pun.
        cookies: Berkas cookies Netscape; membantu menghindari 429.

    Returns:
        0 bila tidak ada yang tersisa, 1 bila masih ada yang gagal.
    """
    from collect_viral_corpus import _fetch_subtitle_text

    if not corpus.is_dir():
        print(f"Direktori korpus tidak ada: {corpus}", file=sys.stderr)
        return 2

    pending: list[tuple[Path, dict[str, object]]] = []
    for path in sorted(corpus.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Lewati %s: %s", path.name, exc)
            continue
        if not isinstance(payload, dict):
            continue
        if not str(payload.get("transcript") or "").strip():
            pending.append((path, payload))

    if not pending:
        print("Semua berkas sudah punya transkrip. Tidak ada yang perlu diisi.")
        return 0

    print(f"Perlu diisi : {len(pending)} berkas")

    if dry_run:
        for path, payload in pending:
            print(f"  {path.name}  (video {payload.get('source_video_id') or '?'})")
        return 0

    filled = failed = 0
    for index, (path, payload) in enumerate(pending):
        video_id = str(payload.get("source_video_id") or "")
        if not video_id:
            logger.warning("Lewati %s: source_video_id kosong.", path.name)
            failed += 1
            continue

        if index:
            time.sleep(DELAY_S)

        transcript = _fetch_subtitle_text(video_id, langs=LANGS, cookies=cookies)
        if not transcript:
            failed += 1
            continue

        payload["transcript"] = transcript
        payload["note"] = "transkrip diisi ulang"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  ok {path.name} ({len(transcript):,} karakter)")
        filled += 1

    print()
    print(f"Terisi  : {filled}")
    print(f"Gagal   : {failed}")
    if failed:
        print()
        print("Yang gagal kemungkinan kena rate limit YouTube (429).")
        print("Jalankan lagi nanti, atau pakai --cookies.")
        return 1
    print()
    print("Korpus lengkap. Lanjutkan dengan:")
    print("  .venv-win\\Scripts\\python.exe scripts\\distill_viral_patterns.py --dry-run")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Titik masuk CLI.

    Args:
        argv: Argumen baris perintah; ``None`` berarti memakai ``sys.argv``.

    Returns:
        Kode keluar proses.
    """
    parser = argparse.ArgumentParser(description="Isi transkrip korpus yang masih kosong.")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS, help="Direktori korpus.")
    parser.add_argument("--dry-run", action="store_true", help="Hanya lapor yang perlu diisi.")
    parser.add_argument("--cookies", help="Berkas cookies Netscape.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    return fill(corpus=args.corpus, dry_run=args.dry_run, cookies=args.cookies)


if __name__ == "__main__":
    raise SystemExit(main())
