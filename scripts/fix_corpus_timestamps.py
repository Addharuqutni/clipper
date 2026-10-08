"""Perbaiki timestamp kasus eval dengan timing subtitle YouTube yang asli.

**Masalah yang diperbaiki.** Korpus viral awalnya menyimpan transkrip sebagai
teks datar tanpa timing. Saat kasus eval dibuat, waktu disebarkan merata ke
durasi video — sebuah perkiraan. Untuk klip komedi 128–172 detik, distribusi
kata tidak merata, sehingga rentang waktunya meleset dan IoU di
``eval_scoring.py`` gagal walaupun momen yang dipilih AI sebenarnya benar.

**Solusinya.** Subtitle otomatis YouTube (format ``json3``) menyimpan
``tStartMs`` per potongan teks — timing asli, bukan perkiraan. Skrip ini
mengambilnya dan menulis ulang ``transcript`` pada kasus eval yang sudah ada.

**Aman diulang.** Kasus yang transkripnya sudah punya timing asli ditandai di
field ``transcript_source`` dan dilewati pada eksekusi berikutnya.

Pemakaian::

    .venv-win\\Scripts\\python.exe scripts\\fix_corpus_timestamps.py --dry-run
    .venv-win\\Scripts\\python.exe scripts\\fix_corpus_timestamps.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

#: Akar repo: ``<repo>/scripts/fix_corpus_timestamps.py``.
REPO_ROOT = Path(__file__).resolve().parents[1]

for _entry in ("apps/worker-render", "packages/shared/src", "apps/worker-light", "apps/api"):
    _path = str(REPO_ROOT / _entry)
    if _path not in sys.path:
        sys.path.insert(0, _path)

logger = logging.getLogger("fix-ts")

#: Direktori kasus eval.
DEFAULT_CASES = REPO_ROOT / "eval" / "scoring" / "cases"

#: Bahasa subtitle yang dicoba berurutan.
LANGS = ("id", "id-ID", "en", "en-US")

#: Jeda antar permintaan, detik.
DELAY_S = 5.0

#: Penanda bahwa transkrip sudah memakai timing asli.
REAL_TIMING = "youtube-json3"


def fetch_timed_transcript(
    video_id: str,
    *,
    langs: tuple[str, ...] = LANGS,
    cookies: str | None = None,
) -> list[tuple[float, str]]:
    """Ambil transkrip bertiming asli dari subtitle YouTube.

    Args:
        video_id: Id video YouTube.
        langs: Bahasa yang dicoba berurutan.
        cookies: Berkas cookies Netscape; membantu menghindari 429.

    Returns:
        Daftar ``(detik, teks)``, atau kosong bila tidak tersedia.
    """
    import httpx
    import yt_dlp

    opts: dict[str, object] = {"quiet": True, "no_warnings": True, "skip_download": True}
    if cookies:
        opts["cookiefile"] = cookies

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore[arg-type]
            info = ydl.extract_info(video_id, download=False)
    except Exception as exc:  # noqa: BLE001 - satu video gagal tidak fatal
        logger.warning("Gagal metadata %s: %s", video_id, exc)
        return []

    manual = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}

    track = None
    for source in (manual, automatic):
        for lang in langs:
            if lang in source and source[lang]:
                track = source[lang]
                break
        if track:
            break
    if not track:
        return []

    chosen = [fmt for fmt in track if fmt.get("ext") == "json3"] or list(track)
    url = str(chosen[0].get("url") or "")
    if not url.lower().startswith(("http://", "https://")):
        return []

    try:
        response = httpx.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=30.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = json.loads(response.text)
    except Exception as exc:  # noqa: BLE001 - subtitle satu klip tidak fatal
        logger.warning("Gagal subtitle %s: %s", video_id, exc)
        return []

    rows: list[tuple[float, str]] = []
    for event in payload.get("events") or []:
        start_ms = event.get("tStartMs")
        if not isinstance(start_ms, (int, float)):
            continue
        text = "".join(str(seg.get("utf8") or "") for seg in (event.get("segs") or []))
        text = " ".join(text.split())
        if text:
            rows.append((float(start_ms) / 1000.0, text))
    return rows


def render_lines(rows: list[tuple[float, str]], *, bucket_s: float = 12.0) -> str:
    """Susun baris transkrip bertimestamp dengan pengelompokan tetap.

    Format keluarannya sengaja sama dengan ``_render_transcript`` di produksi
    (``[12.4s] teks ...``), tetapi waktunya berasal dari subtitle asli.

    Args:
        rows: Daftar ``(detik, teks)``.
        bucket_s: Lebar bucket, detik.

    Returns:
        Teks transkrip berformat baris.
    """
    lines: list[str] = []
    bucket: list[str] = []
    bucket_start: float | None = None

    for start, text in rows:
        if bucket_start is None:
            bucket_start = start
        bucket.append(text)
        if start - bucket_start >= bucket_s:
            lines.append(f"[{bucket_start:.1f}s] {' '.join(bucket)}")
            bucket = []
            bucket_start = None

    if bucket and bucket_start is not None:
        lines.append(f"[{bucket_start:.1f}s] {' '.join(bucket)}")
    return "\n".join(lines)


def fix(*, cases: Path, dry_run: bool, cookies: str | None, force: bool) -> int:
    """Perbaiki timestamp semua kasus eval yang masih memakai perkiraan.

    Args:
        cases: Direktori kasus.
        dry_run: Bila ``True``, hanya lapor tanpa mengambil apa pun.
        cookies: Berkas cookies Netscape.
        force: Bila ``True``, perbaiki juga yang sudah bertiming asli.

    Returns:
        0 bila tidak ada yang gagal, 1 bila ada.
    """
    if not cases.is_dir():
        print(f"Direktori kasus tidak ada: {cases}", file=sys.stderr)
        return 2

    pending: list[tuple[Path, dict[str, object]]] = []
    for path in sorted(cases.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        video_id = str(payload.get("source_video_id") or "")
        if not video_id:
            continue
        if payload.get("transcript_source") == REAL_TIMING and not force:
            continue
        pending.append((path, payload))

    if not pending:
        print("Semua kasus sudah memakai timing asli.")
        return 0

    print(f"Perlu diperbaiki : {len(pending)} kasus")

    if dry_run:
        for path, payload in pending:
            print(f"  {path.name}  (video {payload.get('source_video_id')})")
        return 0

    fixed = failed = 0
    for index, (path, payload) in enumerate(pending):
        video_id = str(payload.get("source_video_id") or "")
        if index:
            time.sleep(DELAY_S)

        rows = fetch_timed_transcript(video_id, cookies=cookies)
        if not rows:
            failed += 1
            print(f"  gagal {path.name}")
            continue

        payload["transcript"] = render_lines(rows)
        payload["transcript_source"] = REAL_TIMING
        payload["timed_segments"] = len(rows)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"  ok {path.name} ({len(rows)} potongan bertiming)")
        fixed += 1

    print()
    print(f"Diperbaiki : {fixed}")
    print(f"Gagal      : {failed}")
    if failed:
        print()
        print("Jalankan lagi nanti bila gagal karena rate limit.")
        return 1
    print()
    print("Ukur ulang dengan:")
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
        description="Perbaiki timestamp kasus eval dengan timing subtitle asli."
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="Direktori kasus.")
    parser.add_argument("--dry-run", action="store_true", help="Hanya lapor.")
    parser.add_argument("--cookies", help="Berkas cookies Netscape.")
    parser.add_argument("--force", action="store_true", help="Perbaiki juga yang sudah bertiming.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    return fix(cases=args.cases, dry_run=args.dry_run, cookies=args.cookies, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
