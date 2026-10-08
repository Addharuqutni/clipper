"""Kumpulkan korpus klip berkinerja tinggi dari YouTube lewat yt-dlp.

**Mengapa yt-dlp, bukan YouTube Data API.** Repo sudah memakai yt-dlp, dan
yt-dlp bisa mencari serta mengambil metadata **tanpa API key** — sehingga tidak
perlu menambah kredensial baru. Harga yang dibayar: tidak ada kuota resmi, jadi
pemanggilan dibatasi sendiri dengan jeda dan pagar.

**Yang dikumpulkan.** Video pendek (klip), beserta view count dan transkripnya
bila tersedia. Hasilnya ditulis ke ``eval/viral/clips/`` dalam format yang bisa
langsung dibaca ``scripts/distill_viral_patterns.py``.

**Jebakan terbesar — jangan dilatih pada sinyal palsu.** Dua aturan yang
menentukan korpus ini berguna atau menyesatkan:

1. **Satuan harus klip.** Melabel video 60 menit sebagai "viral" tidak memberi
   tahu model momen MANA yang bagus. Karena itu durasi dibatasi.
2. **Jangan urutkan menurut view count mentah.** Itu terkontaminasi ukuran
   channel: video dari channel besar lebih sering viral, dan model akan belajar
   mengenali **gaya bicara channel besar**, bukan momen yang bagus. Skrip ini
   menghitung **rasio terhadap median channel** dan bisa menyortir menurut itu.

Pemakaian::

    .venv-win\\Scripts\\python.exe scripts\\collect_viral_corpus.py --query "podcast" --limit 20 --dry-run
    .venv-win\\Scripts\\python.exe scripts\\collect_viral_corpus.py --channel "@DeddyCorbuzier" --limit 30
    .venv-win\\Scripts\\python.exe scripts\\collect_viral_corpus.py --query "podcast" --sort-by ratio
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

import httpx

#: Akar repo: ``<repo>/scripts/collect_viral_corpus.py``.
REPO_ROOT = Path(__file__).resolve().parents[1]

for _entry in ("apps/worker-render", "packages/shared/src", "apps/worker-light", "apps/api"):
    _path = str(REPO_ROOT / _entry)
    if _path not in sys.path:
        sys.path.insert(0, _path)

logger = logging.getLogger("collect")

#: Direktori korpus keluaran.
DEFAULT_OUT = REPO_ROOT / "eval" / "viral" / "clips"

#: Batas durasi klip, detik. Di atas ini bukan klip — melabel video 60 menit
#: sebagai "viral" tidak memberi tahu model momen mana yang bagus.
MAX_CLIP_S = 180.0

#: Batas durasi minimum, detik. Di bawah ini transkripnya terlalu sedikit.
MIN_CLIP_S = 15.0

#: Bahasa subtitle yang dicoba berurutan.
SUBTITLE_LANGS = ("id", "id-ID", "en", "en-US", "en-GB")

#: Jeda antar pemanggilan, detik. Tidak ada kuota resmi di jalur ini, jadi
#: pembatasan dilakukan sendiri supaya tidak membebani YouTube. Dinaikkan dari
#: 1,5 setelah pengumpulan nyata memicu HTTP 429 dari YouTube.
REQUEST_DELAY_S = 4.0

#: Jeda tambahan khusus pengunduhan subtitle, detik. Subtitle diambil dari
#: endpoint ``timedtext`` yang jauh lebih sensitif terhadap laju permintaan
#: daripada metadata.
SUBTITLE_DELAY_S = 6.0

#: Percobaan ulang untuk kegagalan sementara (429/5xx), dengan jeda
#: eksponensial. Pengumpulan korpus memang lambat; lebih baik lambat daripada
#: kehilangan separuh korpus karena rate limit.
MAX_RETRIES = 3
RETRY_BASE_S = 10.0

#: User-Agent untuk pengunduhan subtitle. Endpoint ``timedtext`` menolak
#: permintaan tanpa UA yang lazim.
SUBTITLE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

#: Pagar jumlah kandidat yang diproses metadata lengkapnya.
MAX_METADATA_CALLS = 40


@dataclass(frozen=True, slots=True)
class Candidate:
    """Satu kandidat klip sebelum transkripnya diambil.

    Attributes:
        video_id: Id video YouTube.
        title: Judul video.
        duration_s: Durasi, detik.
        view_count: Jumlah tayang; ``None`` bila tidak tersedia.
        channel: Nama channel.
        channel_median_views: Median tayang channel ini; ``None`` bila channel
            tidak dikenal (misal hasil pencarian tanpa konteks channel).
    """

    video_id: str
    title: str
    duration_s: float
    view_count: int | None
    channel: str
    channel_median_views: float | None = None

    @property
    def ratio(self) -> float:
        """Rasio tayang terhadap median channel.

        Returns:
            Rasio; ``0.0`` bila median tidak tersedia, karena rasio tanpa
            pembanding tidak bisa dipakai untuk memilih.
        """
        if self.channel_median_views is None or not self.channel_median_views:
            return 0.0
        if self.view_count is None:
            return 0.0
        return self.view_count / self.channel_median_views


def _ydl_opts(*, flat: bool, cookies: str | None = None) -> dict[str, object]:
    """Opsi yt-dlp.

    Args:
        flat: Bila ``True``, hanya daftar entri tanpa memproses tiap video
            (jauh lebih cepat, cukup untuk view count).
        cookies: Jalur berkas cookies Netscape; ``None`` berarti tanpa cookies.

    Returns:
        Kamus opsi.
    """
    opts: dict[str, object] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": flat,
        "noplaylist": True,
    }
    if cookies:
        opts["cookiefile"] = cookies
    return opts


def search(query: str, limit: int) -> list[dict[str, object]]:
    """Cari video berdasar kata kunci.

    Args:
        query: Kata kunci pencarian.
        limit: Jumlah maksimum hasil.

    Returns:
        Daftar entri mentah yt-dlp.
    """
    import yt_dlp

    with yt_dlp.YoutubeDL(_ydl_opts(flat=True)) as ydl:  # type: ignore[arg-type]
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    entries = (info or {}).get("entries") or []
    return [entry for entry in entries if isinstance(entry, dict) and entry.get("id")]


def list_channel(channel: str, limit: int, *, cookies: str | None = None) -> list[dict[str, object]]:
    """Ambil daftar video dari satu channel.

    Dipakai untuk menghitung **median tayang channel**, yang menjadi pembanding
    rasio. Tanpa median, view count mentah tidak bisa dibedakan dari sekadar
    "channel-nya besar".

    Args:
        channel: Handle channel (``@nama``) atau URL kanal.
        limit: Jumlah maksimum entri.

    Returns:
        Daftar entri mentah yt-dlp.
    """
    import yt_dlp

    # Tiga bentuk masukan diterima: URL lengkap, handle ``@nama``, atau id
    # channel ``UC...``. URL channel-id tanpa awalan http tidak dikenali
    # yt-dlp, jadi dilengkapi dulu menjadi URL /videos.
    if channel.startswith("http"):
        url = channel.rstrip("/") + ("" if channel.rstrip("/").endswith("/videos") else "/videos")
    elif channel.startswith("UC"):
        url = f"https://www.youtube.com/channel/{channel}/videos"
    else:
        url = f"https://www.youtube.com/{channel}/videos"

    with yt_dlp.YoutubeDL(_ydl_opts(flat=True, cookies=cookies)) as ydl:  # type: ignore[arg-type]
        info = ydl.extract_info(url, download=False, process=False)
    entries = (info or {}).get("entries") or []
    return [entry for entry in entries if isinstance(entry, dict) and entry.get("id")][:limit]


def _resolve_channel_name(entry: dict[str, object], fallback: str | None) -> str:
    """Tentukan nama channel yang bisa dibaca manusia.

    ``extract_flat`` sering tidak mengisi ``channel``, sehingga korpus
    berakhir berisi id teknis seperti ``UCiq_eB1...`` yang tidak membantu
    saat meninjau. Bila metadata entri kosong, nama diambil dari argumen
    yang diberikan pengguna.

    Args:
        entry: Entri mentah yt-dlp.
        fallback: Nilai dari argumen ``--channel``; boleh ``None``.

    Returns:
        Nama channel, atau string kosong bila tidak ada sama sekali.
    """
    name = entry.get("channel") or entry.get("uploader")
    if isinstance(name, str) and name.strip():
        return name.strip()
    if fallback:
        # Handle ``@nama`` lebih mudah dibaca daripada id channel.
        return fallback.lstrip("@") if not fallback.startswith("UC") else fallback
    return ""


def _fetch_duration(video_id: str, *, cookies: str | None = None) -> float:
    """Ambil durasi satu video dari metadata lengkap.

    Args:
        video_id: Id video YouTube.

    Returns:
        Durasi dalam detik; ``0.0`` bila gagal, supaya pemanggil bisa
        membedakan "tidak terbaca" dari durasi nyata.
    """
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL(_ydl_opts(flat=False, cookies=cookies)) as ydl:  # type: ignore[arg-type]
            info = ydl.extract_info(video_id, download=False)
    except Exception as exc:  # noqa: BLE001 - satu video gagal tidak fatal
        logger.warning("Gagal mengambil durasi %s: %s", video_id, exc)
        return 0.0

    duration = (info or {}).get("duration")
    return float(duration) if isinstance(duration, (int, float)) else 0.0


def _fetch_subtitle_text(
    video_id: str, *, langs: tuple[str, ...], cookies: str | None = None
) -> str:
    """Ambil teks subtitle satu video.

    Args:
        video_id: Id video YouTube.
        langs: Bahasa yang dicoba berurutan.

    Returns:
        Teks transkrip, atau string kosong bila tidak tersedia.
    """
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL(_ydl_opts(flat=False, cookies=cookies)) as ydl:  # type: ignore[arg-type]
            info = ydl.extract_info(video_id, download=False)
    except Exception as exc:  # noqa: BLE001 - satu video gagal tidak boleh menghentikan korpus
        logger.warning("Gagal mengambil metadata %s: %s", video_id, exc)
        return ""

    manual = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}

    track: list[dict[str, object]] | None = None
    used_lang = ""
    for source in (manual, automatic):
        for lang in langs:
            if lang in source and source[lang]:
                track = source[lang]  # type: ignore[assignment]
                used_lang = lang
                break
        if track:
            break

    if not track:
        return ""

    chosen = [fmt for fmt in track if fmt.get("ext") == "json3"] or list(track)
    url = str(chosen[0].get("url") or "")
    if not url:
        return ""

    # URL berasal dari metadata yang diunduh, jadi harus divalidasi sebelum
    # dibuka: hanya http/https yang diizinkan.
    if not url.lower().startswith(("http://", "https://")):
        logger.warning("URL subtitle tidak lazim pada %s, dilewati.", video_id)
        return ""

    # Endpoint timedtext mudah mengembalikan 429 bila dipanggil beruntun.
    # Ulang dengan jeda eksponensial; kalau tetap gagal, klip ini dilewati
    # tanpa menghentikan seluruh korpus.
    payload: dict[str, object] | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Skema sudah divalidasi di atas (hanya http/https). httpx dipakai
            # (bukan urllib) supaya konsisten dengan klien lain di repo dan
            # tidak perlu mengabaikan peringatan keamanan.
            response = httpx.get(
                url,
                headers={"User-Agent": SUBTITLE_USER_AGENT},
                timeout=30.0,
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = json.loads(response.text)
            break
        except Exception as exc:  # noqa: BLE001 - subtitle satu klip tidak fatal
            if attempt == MAX_RETRIES:
                logger.warning("Gagal mengunduh subtitle %s (%s): %s", video_id, used_lang, exc)
                return ""
            delay = RETRY_BASE_S * 2 ** (attempt - 1)
            logger.info(
                "Subtitle %s gagal sementara (%s); ulang dalam %.0fs (%d/%d).",
                video_id, exc, delay, attempt, MAX_RETRIES,
            )
            time.sleep(delay)

    if payload is None:
        return ""

    parts: list[str] = []
    for event in payload.get("events") or []:
        for segment in event.get("segs") or []:
            text = str(segment.get("utf8") or "").strip()
            if text and text != "\n":
                parts.append(text)
    return " ".join(parts)


def _slugify(title: str, video_id: str) -> str:
    """Ubah judul menjadi nama berkas aman.

    Args:
        title: Judul video.
        video_id: Id video, dipakai bila judul tidak berguna.

    Returns:
        Slug yang aman untuk nama berkas.
    """
    cleaned = "".join(char if char.isalnum() else "-" for char in (title or "").lower()).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return (cleaned[:48] or f"video-{video_id}") + f"-{video_id[:8]}"


def collect(
    *,
    query: str | None,
    channel: str | None,
    limit: int,
    out: Path,
    dry_run: bool,
    sort_by: str,
    with_subtitles: bool,
    min_clip_s: float = MIN_CLIP_S,
    max_clip_s: float = MAX_CLIP_S,
    cookies: str | None = None,
) -> int:
    """Kumpulkan korpus.

    Args:
        query: Kata kunci pencarian; ``None`` bila memakai channel.
        channel: Handle channel; ``None`` bila memakai pencarian.
        limit: Jumlah maksimum kandidat.
        out: Direktori keluaran.
        dry_run: Bila ``True``, hanya tampilkan ringkasan tanpa menulis.
        sort_by: ``views`` atau ``ratio``.
        with_subtitles: Ambil transkrip bila tersedia.
        cookies: Jalur berkas cookies Netscape; membantu menghindari 429.

    Returns:
        Kode keluar proses.
    """
    if not query and not channel:
        print("Salah satu --query atau --channel wajib diisi.", file=sys.stderr)
        return 2

    if cookies:
        try:
            from clipper_shared.youtube_cookies import validate_cookie_file
        except ImportError:  # pragma: no cover - repo selalu punya modul ini
            logger.warning("Modul cookies tidak tersedia; lanjut tanpa cookies.")
        else:
            validation = validate_cookie_file(cookies)
            if not validation.ok:
                print(f"Berkas cookies tidak sah: {validation.reason}", file=sys.stderr)
                return 2

    entries = search(query, limit) if query else list_channel(channel or "", limit, cookies=cookies)
    if not entries:
        print("Tidak ada hasil.", file=sys.stderr)
        return 2

    # Median channel: pembanding untuk rasio. Diambil dari daftar entri itu
    # sendiri bila memakai channel, atau None bila hasil pencarian.
    medians: dict[str, float] = {}
    if channel:
        counts = [
            int(entry["view_count"])
            for entry in entries
            if isinstance(entry.get("view_count"), (int, float))
        ]
        if counts:
            medians["*"] = float(statistics.median(counts))

    candidates: list[Candidate] = []
    for entry in entries:
        video_id = str(entry.get("id") or "")
        duration = entry.get("duration")
        duration_s = float(duration) if isinstance(duration, (int, float)) else 0.0
        views = entry.get("view_count")
        view_count = int(views) if isinstance(views, (int, float)) else None
        candidates.append(
            Candidate(
                video_id=video_id,
                title=str(entry.get("title") or ""),
                duration_s=duration_s,
                view_count=view_count,
                channel=_resolve_channel_name(entry, channel),
                channel_median_views=medians.get("*") if channel else None,
            )
        )

    # ``extract_flat`` tidak mengisi durasi pada hasil pencarian, jadi durasi
    # harus diambil dari metadata lengkap. Ini biaya yang tidak bisa dihindari:
    # tanpa durasi, penyaring "klip bukan video utuh" tidak bisa dijalankan.
    needs_duration = [c for c in candidates if c.duration_s <= 0.0]
    if needs_duration:
        capped = needs_duration[:MAX_METADATA_CALLS]
        print(f"Mengambil durasi {len(capped)} kandidat...")
        for index, candidate in enumerate(capped):
            if index:
                time.sleep(REQUEST_DELAY_S)
            fetched = _fetch_duration(candidate.video_id, cookies=cookies)
            if fetched > 0.0:
                candidates[candidates.index(candidate)] = replace(candidate, duration_s=fetched)

    # Saring durasi: yang masuk korpus harus klip, bukan video utuh.
    clipped = [c for c in candidates if min_clip_s <= c.duration_s <= max_clip_s]
    skipped_duration = len(candidates) - len(clipped)

    if sort_by == "ratio":
        clipped.sort(key=lambda c: c.ratio, reverse=True)
    else:
        clipped.sort(key=lambda c: (c.view_count or 0), reverse=True)

    print(f"Kandidat        : {len(candidates)}")
    print(f"Lolos durasi    : {len(clipped)} (dilewati {skipped_duration} di luar {min_clip_s:.0f}-{max_clip_s:.0f}s)")
    print(f"Urutan          : {sort_by}")

    if dry_run:
        print()
        header = f"{'judul':<44} {'durasi':>7} {'views':>12} {'rasio':>7}"
        print(header)
        print("-" * len(header))
        for candidate in clipped[:15]:
            print(
                f"{candidate.title[:43]:<44} {candidate.duration_s:>6.0f}s "
                f"{candidate.view_count or 0:>12,} {candidate.ratio:>7.2f}"
            )
        if not channel:
            print()
            print("Catatan: rasio 0,00 karena tidak ada median channel pada hasil pencarian.")
            print("Pakai --channel <handle> bila ingin memilih menurut rasio.")
        return 0

    out.mkdir(parents=True, exist_ok=True)
    written = skipped = 0

    for index, candidate in enumerate(clipped[:MAX_METADATA_CALLS]):
        if index:
            time.sleep(REQUEST_DELAY_S)

        transcript = ""
        if with_subtitles:
            # Jeda khusus sebelum mengunduh subtitle: endpoint timedtext jauh
            # lebih sensitif terhadap laju permintaan daripada metadata.
            if index:
                time.sleep(SUBTITLE_DELAY_S)
            transcript = _fetch_subtitle_text(
                candidate.video_id, langs=SUBTITLE_LANGS, cookies=cookies
            )

        if not transcript:
            skipped += 1
            logger.info("Lewati %s: subtitle tidak tersedia.", candidate.video_id)
            continue

        payload = {
            "slug": _slugify(candidate.title, candidate.video_id),
            "source_video_id": candidate.video_id,
            "channel": candidate.channel,
            "duration_s": round(candidate.duration_s, 1),
            "view_count": candidate.view_count,
            "view_ratio_to_channel_median": round(candidate.ratio, 2),
            "note": "dikumpulkan otomatis; periksa sebelum dipakai",
            "transcript": transcript,
        }
        path = out / f"{payload['slug']}.json"
        if path.exists():
            skipped += 1
            continue
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  tulis {path.name} ({len(transcript):,} karakter transkrip)")
        written += 1

    print()
    print(f"Ditulis : {written}")
    print(f"Dilewati: {skipped}")
    if written:
        print()
        print("Periksa dulu isinya, lalu jalankan:")
        print("  .venv-win\\Scripts\\python.exe scripts\\distill_viral_patterns.py --dry-run")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Titik masuk CLI.

    Args:
        argv: Argumen baris perintah; ``None`` berarti memakai ``sys.argv``.

    Returns:
        Kode keluar proses.
    """
    parser = argparse.ArgumentParser(
        description="Kumpulkan korpus klip berkinerja tinggi dari YouTube."
    )
    parser.add_argument("--query", help="Kata kunci pencarian.")
    parser.add_argument("--channel", help="Handle channel, misal @nama.")
    parser.add_argument("--limit", type=int, default=20, help="Jumlah kandidat.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Direktori keluaran.")
    parser.add_argument("--dry-run", action="store_true", help="Hanya tampilkan ringkasan.")
    parser.add_argument(
        "--sort-by", choices=("views", "ratio"), default="views", help="Urutan pemilihan."
    )
    parser.add_argument(
        "--no-subtitles", action="store_true", help="Lewati pengambilan transkrip."
    )
    parser.add_argument(
        "--cookies",
        help="Berkas cookies Netscape. Membantu menghindari HTTP 429 pada subtitle.",
    )
    parser.add_argument(
        "--min-duration",
        type=float,
        default=MIN_CLIP_S,
        help=f"Durasi minimum klip, detik (bawaan {MIN_CLIP_S:.0f}).",
    )
    parser.add_argument(
        "--max-duration",
        type=float,
        default=MAX_CLIP_S,
        help=f"Durasi maksimum klip, detik (bawaan {MAX_CLIP_S:.0f}).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    return collect(
        query=args.query,
        channel=args.channel,
        limit=args.limit,
        out=args.out,
        dry_run=args.dry_run,
        sort_by=args.sort_by,
        with_subtitles=not args.no_subtitles,
        cookies=args.cookies,
        min_clip_s=args.min_duration,
        max_clip_s=args.max_duration,
    )


if __name__ == "__main__":
    raise SystemExit(main())
