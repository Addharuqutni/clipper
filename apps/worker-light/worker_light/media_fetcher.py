"""Pengambilan media sumber: berkas unggahan atau video YouTube.

Dua jalur dengan sifat berbeda:

* **Unggahan** — berkas sudah ada di penyimpanan lokal; cukup dibaca di tempat.
* **YouTube** — perlu ``yt-dlp``, dan di sinilah semua kesulitan terkonsentrasi:
  pembatasan bot, video yang memerlukan login, dan kebutuhan cookies.

**Prioritas subtitle.** Bila video YouTube sudah punya subtitle, ia dipakai dan
Whisper dilewati sepenuhnya. Ini bukan optimasi kecil: pada CPU, Whisper
berjalan 0,5–0,9x realtime (TECH_SPEC §0.1), sedangkan mengambil subtitle selesai
dalam hitungan detik. Untuk video 30 menit, bedanya bisa satu jam.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from clipper_shared.processes import run_process
from clipper_shared.subtitles import SubtitleTrack, parse_subtitle, pick_track

logger = logging.getLogger(__name__)

#: Timeout operasi yt-dlp. Video panjang pada koneksi lambat butuh waktu.
YTDLP_TIMEOUT_S = int(os.getenv("YTDLP_TIMEOUT_S", "1800"))


class IngestError(RuntimeError):
    """Kegagalan ingest dengan pesan yang layak ditampilkan ke pengguna."""


@dataclass
class YoutubeMetadata:
    """Metadata video YouTube hasil ``yt-dlp -J``."""

    video_id: str
    title: str
    duration_s: float
    is_live: bool
    is_private: bool
    uploader: str = ""
    #: Bahasa ucapan video menurut yt-dlp (mis. ``id``), bila diketahui.
    language: str | None = None
    #: Trek subtitle yang tersedia, terpisah antara manual dan otomatis.
    manual_tracks: list[SubtitleTrack] = field(default_factory=list)
    automatic_tracks: list[SubtitleTrack] = field(default_factory=list)

    @property
    def has_subtitles(self) -> bool:
        return bool(self.manual_tracks or self.automatic_tracks)


def _resolve_binary(name: str, env_var: str) -> str:
    """Cari binary di PATH, lalu di samping interpreter yang sedang berjalan."""
    override = os.getenv(env_var)
    if override:
        return override

    candidates = [name]
    if sys.platform == "win32" and not name.lower().endswith(".exe"):
        candidates.insert(0, f"{name}.exe")

    for cand_name in candidates:
        found = shutil.which(cand_name)
        if found:
            return found

        # Cari di samping interpreter aktif (venv/bin/, Scripts/ di Windows).
        candidate = Path(sys.executable).parent / cand_name
        if candidate.is_file():
            return str(candidate)

        # Jalur umum venv proyek
        for base in (Path.cwd(), *Path.cwd().parents):
            for folder in (".venv-worker/bin", ".venv/bin", ".venv-win/Scripts"):
                fallback = base / folder / cand_name
                if fallback.is_file():
                    return str(fallback)

    return candidates[0]


def ytdlp_executable() -> str:
    """Jalur yt-dlp yang dapat dijalankan, dapat ditimpa lewat ``YTDLP_BINARY``."""
    return _resolve_binary("yt-dlp", "YTDLP_BINARY")


def _is_safe_subtitle_url(url: str) -> bool:
    """Hanya izinkan http/https untuk pengunduhan subtitle.

    Subtitle datang dari metadata yang dikeluarkan yt-dlp, tetapi memeriksa
    skemanya tetap perlu: ``urlopen`` juga menerima ``file:`` yang akan membaca
    berkas lokal server.
    """
    from urllib.parse import urlparse

    return urlparse(url).scheme in {"http", "https"}


def _ffmpeg_dir() -> str | None:
    """Direktori berisi FFmpeg, untuk diteruskan ke ``yt-dlp --ffmpeg-location``.

    **Mengapa ini perlu.** ``yt-dlp`` memerlukan FFmpeg untuk menggabungkan
    stream video dan audio terpisah (format DASH). Bila FFmpeg tidak ada di
    ``PATH`` proses, yt-dlp melaporkan ``exe versions: none`` dan **melewati
    tahap merge tanpa gagal** — ia mengembalikan 0 dengan dua berkas terpisah
    (``source.f399.mp4`` untuk video dan ``source.f251.webm`` untuk audio).

    Akibatnya ``download_youtube`` memilih berkas pertama secara alfabet
    (``source.f251.webm``, audio saja) dan ``probe_media`` gagal dengan
    "Berkas tidak memuat trek video." Gejalanya menyesatkan karena unduhan
    sendiri tampak berhasil.

    Mengarahkan ke direktori FFmpeg lewat ``FFMPEG_BINARY`` (konvensi yang sama
    dengan ``storage._binary``) menutup celah ini tanpa mengandalkan PATH.

    Returns:
        Direktori FFmpeg, atau ``None`` bila tidak dapat ditentukan (biarkan
        yt-dlp memakai PATH seperti sebelumnya).
    """
    from clipper_shared.storage import binary

    ffmpeg = binary("ffmpeg")
    parent = Path(ffmpeg).parent
    return str(parent) if parent != Path() and parent.is_dir() else None


def _ytdlp_base_args(cookies_path: Path | None) -> list[str]:
    """Argumen dasar yt-dlp, termasuk cookies bila diberikan.

    ``--no-playlist`` penting: tanpa itu, URL yang menunjuk ke playlist akan
    mengunduh seluruh playlist, dan itu menghabiskan disk serta waktu.

    ``--retries`` dan ``--fragment-retries`` dinaikkan dari bawaan (10) karena
    YouTube membalas ``HTTP 403`` secara **intermiten** pada transfer data —
    terbukti saat pengujian: perintah yang sama bisa gagal 403 lalu berhasil
    pada percobaan berikutnya, tanpa perubahan apa pun. Tanpa ini, satu 403
    sesaat menggagalkan seluruh job karena ``ingest_media`` hanya me-retry
    ``ConnectionError`` (bukan ``IngestError``). yt-dlp sendiri lebih tepat
    menangani ini: ia melanjutkan dari offset yang sudah terunduh.
    """
    args = [
        ytdlp_executable(),
        "--no-playlist",
        "--no-warnings",
        "--no-progress",
        "--retries", "25",
        "--fragment-retries", "25",
        "--socket-timeout", "30",
    ]
    ffmpeg_dir = _ffmpeg_dir()
    if ffmpeg_dir is not None:
        args.extend(["--ffmpeg-location", ffmpeg_dir])
    if cookies_path is not None and cookies_path.exists():
        args.extend(["--cookies", str(cookies_path)])
    # YouTube memakai tantangan JavaScript; tanpa runtime JS yt-dlp hanya
    # memperingatkan "some formats may be missing" (tersembunyi oleh
    # --no-warnings) lalu lanjut dengan format yang tersisa. Bawaan yt-dlp hanya
    # mencari deno, sedangkan node sudah wajib terpasang untuk frontend.
    # Skrip pemecahnya datang dari paket yt-dlp-ejs (extra `yt-dlp[default]`).
    import shutil

    if shutil.which("node"):
        args.extend(["--js-runtimes", "node"])
    return args


def fetch_youtube_metadata(url: str, cookies_path: Path | None = None, *, job_id: str) -> YoutubeMetadata:
    """Ambil metadata video tanpa mengunduh isinya.

    ``-J`` mengeluarkan JSON lengkap termasuk daftar subtitle. Ini dipakai untuk
    memvalidasi lebih dulu: durasi, status live, dan keberadaan subtitle — semua
    sebelum menyentuh berkas besar.
    """
    result = run_process(
        # "--" wajib: tanpa itu, nilai berawalan "-" dibaca sebagai opsi yt-dlp
        # (mis. --config-locations / --exec), bukan sebagai URL.
        [*_ytdlp_base_args(cookies_path), "-J", "--", url],
        job_id=job_id,
        capture_output=True,
        text=True,
        timeout_s=YTDLP_TIMEOUT_S,
    )

    if result.returncode != 0:
        raise IngestError(_translate_ytdlp_error(result.stderr))

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise IngestError(f"yt-dlp mengembalikan keluaran yang tidak dapat dibaca: {exc}") from exc

    manual, automatic = _collect_tracks(data)

    return YoutubeMetadata(
        video_id=str(data.get("id") or ""),
        title=str(data.get("title") or ""),
        duration_s=float(data.get("duration") or 0.0),
        is_live=bool(data.get("is_live")),
        is_private=str(data.get("availability") or "") in {"private", "premium_only"},
        uploader=str(data.get("uploader") or ""),
        language=str(data.get("language") or "") or None,
        manual_tracks=manual,
        automatic_tracks=automatic,
    )


def _collect_tracks(data: dict[str, object]) -> tuple[list[SubtitleTrack], list[SubtitleTrack]]:
    """Pisahkan trek subtitle manual dari yang otomatis.

    Subtitle manual selalu diutamakan: yang otomatis sering salah mengenali kata,
    dan kesalahan itu akan ikut terbakar ke dalam klip final.
    """
    manual: list[SubtitleTrack] = []
    automatic: list[SubtitleTrack] = []

    # yt-dlp menamai kunci secara berbeda antar versi: `subtitles` dan
    # `automatic_captions` adalah yang dipakai sekarang.
    for key, target in (("subtitles", manual), ("automatic_captions", automatic)):
        raw = data.get(key)
        if not isinstance(raw, dict):
            continue
        for lang, entries in raw.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                ext = str(entry.get("ext") or "")
                url = str(entry.get("url") or "")
                if not url:
                    continue
                # Hanya format yang bisa kami urai; sisanya diabaikan agar
                # pemilihan trek tidak pernah menghasilkan pilihan mati.
                if ext not in {"json3", "srt", "vtt"}:
                    continue
                target.append(
                    SubtitleTrack(
                        lang=str(lang),
                        ext=ext,
                        url=url,
                        name=str(entry.get("name") or ""),
                        automatic=target is automatic,
                    )
                )

    return manual, automatic


def _translate_ytdlp_error(stderr: str) -> str:
    """Ubah pesan yt-dlp menjadi kalimat yang bisa ditindaklanjuti pengguna.

    Pesan mentah yt-dlp panjang dan teknis. Yang paling sering terjadi sudah
    terpetakan di sini, karena setiap penyebab memerlukan tindakan berbeda:
    cookies, URL, atau video yang memang tidak bisa diunduh.
    """
    lowered = (stderr or "").lower()

    if "sign in to confirm" in lowered or "confirm you're not a bot" in lowered:
        return (
            "YouTube meminta verifikasi. Unggah berkas cookies.txt dari browser "
            "Anda yang sudah login ke YouTube, lalu coba lagi."
        )
    if "private video" in lowered:
        return "Video ini bersifat privat dan tidak dapat diakses."
    if "video unavailable" in lowered or "not available" in lowered:
        return "Video tidak tersedia. Periksa apakah tautannya benar dan video masih ada."
    if "age" in lowered and "restrict" in lowered:
        return "Video dibatasi usia. Unggah cookies.txt dari akun yang dapat mengaksesnya."
    if "is live" in lowered or "live event" in lowered:
        return "Video ini sedang siaran langsung. Tunggu sampai selesai, lalu coba lagi."
    if "requested format" in lowered:
        return "Format media tidak tersedia. Coba lagi; bila berulang, laporkan sebagai bug."

    tail = (stderr or "").strip().splitlines()
    detail = tail[-1] if tail else "tidak ada detail"
    return f"Gagal mengambil video: {detail}"


def download_youtube(
    url: str,
    work_dir: Path,
    cookies_path: Path | None = None,
    max_height: int = 1080,
    *,
    job_id: str,
) -> Path:
    """Unduh video YouTube ke direktori kerja.

    ``max_height`` membatasi resolusi: mengunduh 4K lalu memotongnya ke 1080x1920
    hanya membuang bandwidth dan waktu, karena hasil akhirnya tidak akan lebih
    tajam daripada sumbernya setelah crop.
    """
    template = str(work_dir / "source.%(ext)s")
    result = run_process(
        [
            # Binary TIDAK ditulis ulang di sini: `_ytdlp_base_args` sudah
            # menyertakannya. Menambahkannya dua kali membuat yt-dlp menerima
            # path binary sebagai URL dan gagal dengan pesan yang membingungkan
            # ("'/path/to/yt-dlp' is not a valid URL").
            *_ytdlp_base_args(cookies_path),
            "-f", f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b",
            "--merge-output-format", "mp4",
            "-o", template,
            # Jalur berkas akhir (setelah merge) dicetak ke stdout, jadi tidak
            # perlu menebak dari glob — yang bisa memilih potongan audio saja.
            "--print", "after_move:filepath",
            "--",
            url,
        ],
        job_id=job_id,
        capture_output=True,
        text=True,
        timeout_s=YTDLP_TIMEOUT_S,
    )

    if result.returncode != 0:
        raise IngestError(_translate_ytdlp_error(result.stderr))

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    path = Path(lines[-1]) if lines else None
    if path is None or not path.is_file():
        raise IngestError("Unduhan selesai tetapi berkas hasil tidak ditemukan.")
    return path


def download_subtitle(
    track: SubtitleTrack,
    work_dir: Path,
    cookies_path: Path | None = None,
) -> tuple[list[object], str]:
    """Unduh dan uraikan satu trek subtitle.

    Returns:
        ``(words, health)`` — daftar kata dan catatan kualitas.

    Subtitle yang gagal diurai tidak boleh menggagalkan seluruh job: pemanggil
    dapat melanjutkan ke Whisper. Karena itu kegagalan dikembalikan sebagai
    daftar kosong, bukan pengecualian.
    """
    import urllib.request

    # Skema diperiksa SEBELUM Request dibuat. urlopen juga menerima `file:`,
    # yang akan membaca berkas lokal server — batas keamanan yang nyata,
    # bukan formalitas.
    if not _is_safe_subtitle_url(track.url):
        return [], "URL subtitle tidak menggunakan http/https"

    try:
        request = urllib.request.Request(track.url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            payload = response.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 — kegagalan jaringan apa pun diperlakukan sama
        logger.warning("Gagal mengunduh subtitle %s: %s", track.lang, exc)
        return [], f"gagal mengunduh subtitle: {exc}"

    try:
        words = parse_subtitle(payload, track.ext)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Gagal mengurai subtitle %s (%s): %s", track.lang, track.ext, exc)
        return [], f"gagal mengurai subtitle: {exc}"

    if not words:
        return [], "subtitle kosong"

    # Subtitle hanya per-cue (srt/vtt) menghasilkan timestamp per kata hasil
    # pembagian rata — kurang presisi untuk efek karaoke, dan itu harus
    # dilaporkan supaya ekspektasi pengguna sesuai.
    quality = (
        "timestamp per kata (json3)"
        if track.ext == "json3"
        else f"timestamp per segmen, dibagi rata ({track.ext})"
    )
    return list(words), quality


def select_subtitle_track(
    metadata: YoutubeMetadata, preferred_lang: str | None = None
) -> SubtitleTrack | None:
    """Pilih trek subtitle terbaik dari metadata, sesuai bahasa ucapan video."""
    return pick_track(
        metadata.manual_tracks + metadata.automatic_tracks,
        preferred_lang,
        spoken_lang=metadata.language,
    )


def validate_duration(metadata: YoutubeMetadata, max_minutes: int) -> None:
    """Tolak video yang tidak dapat diproses.

    ``max_minutes`` berasal dari ``clipper_shared.ai_provider.max_video_minutes``:
    kapasitas konteks model AI, dibatasi ``MAX_VIDEO_DURATION_MIN``. Dicek di
    sini — sebelum unduhan dan transkripsi — supaya video yang transkripnya pasti
    tidak muat ditolak seketika.

    Raises:
        IngestError: dengan alasan yang bisa ditampilkan langsung ke pengguna.
    """
    if metadata.is_live:
        raise IngestError("Video ini sedang siaran langsung dan belum dapat diproses.")
    if metadata.is_private:
        raise IngestError("Video ini bersifat privat.")
    if metadata.duration_s <= 0:
        raise IngestError("Durasi video tidak dapat dibaca.")
    if metadata.duration_s > max_minutes * 60:
        minutes = int(metadata.duration_s // 60)
        raise IngestError(
            f"Video berdurasi {minutes} menit, melebihi batas {max_minutes} menit "
            "(kapasitas konteks model AI atau MAX_VIDEO_DURATION_MIN)."
        )
