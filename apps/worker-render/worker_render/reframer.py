"""Reframer: ubah video sumber menjadi klip vertikal 9:16 dengan tiga mode.

Modul ini adalah penggabung seluruh bagian:

* :mod:`worker_render.ffmpeg_pipe` — eksekusi FFmpeg yang tidak macet.
* :mod:`worker_render.face_tracker` — deteksi wajah + sinyal bicara.
* :mod:`clipper_shared.reframe` — geometri, penghalusan crop, rantai filter.
* :mod:`clipper_shared.subtitles` — berkas ASS karaoke.

Tiga mode (permintaan pengguna):

* ``face_track``   — crop mengikuti pembicara aktif.
* ``black_bars``   — video utuh, bar hitam di atas-bawah.
* ``blurred_fill`` — video utuh, latar blur dari video itu sendiri.

Untuk dua mode terakhir, tidak ada deteksi wajah sama sekali: seluruh bingkai
dipertahankan, jadi pelacakan hanya menambah biaya tanpa manfaat.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clipper_shared.processes import run_process
from clipper_shared.reframe import (
    CropMode,
    CropSmoother,
    build_filter_chain,
    crop_width_for,
    plan_letterbox,
)
from clipper_shared.subtitles import SubtitleStyle, SubtitleWord, render_ass

from worker_render.face_tracker import FaceTracker, choose_subject
from worker_render.ffmpeg_pipe import (
    ffmpeg_executable,
    ffprobe_executable,
    run_ffmpeg,
)

logger = logging.getLogger(__name__)

_TEMP_COMMANDS = "crop_positions.txt"
_TEMP_SUBS = "captions.ass"


@dataclass
class SourceInfo:
    """Metadata video sumber hasil probe."""

    width: int
    height: int
    fps: float
    duration_s: float
    has_audio: bool
    frame_count: int


@dataclass
class ReframeOptions:
    """Parameter satu render klip."""

    mode: CropMode = CropMode.FACE_TRACK
    start_s: float = 0.0
    end_s: float = 0.0
    #: Crf dan preset dari env (TECH_SPEC §4.3): preview dan final berbeda tajam.
    crf: int = 18
    preset: str = "fast"
    output_width: int = 1080
    output_height: int = 1920
    ffmpeg_threads: int = 2
    face_model_path: str = ""


def probe_source(path: str | Path, *, job_id: str | None = None) -> SourceInfo:
    """Baca metadata video dengan ffprobe.

    Memakai ``ffprobe`` daripada OpenCV karena akurat untuk ``fps`` (angka
    pecahan seperti 29,97) dan dapat memberi tahu ada tidaknya trek audio —
    informasi yang menentukan langkah mux di akhir.
    """
    result = run_process(
        [
            ffprobe_executable(),
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        job_id=job_id,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe gagal membaca {path}: {result.stderr[-500:]}")

    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise RuntimeError(f"Berkas {path} tidak memuat trek video.")

    fps = _parse_fraction(video.get("avg_frame_rate") or video.get("r_frame_rate") or "30/1")
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0.0)
    frame_count = int(video.get("nb_frames") or (duration * fps if duration else 0))

    # FFmpeg memutar frame sesuai metadata rotasi saat decode (video HP), jadi
    # semua filter dan pembacaan frame bekerja pada ukuran TAMPILAN.
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if _rotation(video) % 180 == 90:
        width, height = height, width

    return SourceInfo(
        width=width,
        height=height,
        fps=fps or 30.0,
        duration_s=duration,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        frame_count=frame_count,
    )


def _rotation(video_stream: dict[str, Any]) -> int:
    """Rotasi tampilan (0/90/180/270) dari tag ``rotate`` atau side data."""
    raw: Any = (video_stream.get("tags") or {}).get("rotate")
    for side in video_stream.get("side_data_list") or []:
        if isinstance(side, dict) and "rotation" in side:
            raw = side["rotation"]
    try:
        return int(round(float(raw or 0))) % 360
    except (TypeError, ValueError):
        return 0


def _parse_fraction(value: str) -> float:
    """Ubah "30000/1001" menjadi 29.97; toleran terhadap "30" biasa."""
    if "/" not in value:
        try:
            return float(value)
        except ValueError:
            return 0.0
    numerator, _, denominator = value.partition("/")
    try:
        denominator_value = float(denominator)
        if denominator_value == 0:
            return 0.0
        return float(numerator) / denominator_value
    except ValueError:
        return 0.0


def default_model_path() -> str:
    """Model MediaPipe: ``FACE_LANDMARKER_MODEL`` atau ``<repo>/.models/face_landmarker.task``."""
    from clipper_shared.storage import repo_path

    return str(repo_path("FACE_LANDMARKER_MODEL", ".models/face_landmarker.task"))


def escape_filter_path(path: Path) -> str:
    """Lolosikan jalur untuk opsi filter FFmpeg **tanpa kutip**.

    Dipakai oleh ``sendcmd=f=...`` di :func:`build_video_filter`.

    **Kenapa titik dua perlu DUA garis miring (``\\\\:``).** Nilai opsi filter
    tanpa kutip melewati dua lapis pembacaan: pemindai opsi filter (yang
    memisahkan ``nama=nilai`` memakai ``:`` dan ``,``) lalu pemindai
    escape-string di dalamnya. Satu garis miring hanya menunda titik dua ke
    lapis pertama — lapis kedua tetap membacanya sebagai pemisah opsi. Pada
    jalur Windows seperti ``D:\\PROJECT\\...`` FFmpeg berhenti di huruf drive:

        No option name near '/PROJECT/clipper/...'
        Error parsing filterchain 'sendcmd=f=D\\:/PROJECT/...'

    Pesan itu menyebut teks *setelah* titik dua, sehingga penyebabnya tidak
    terlihat. Diuji langsung terhadap FFmpeg di host ini: ``\\:`` gagal,
    ``\\\\:`` berhasil.

    **JANGAN pakai fungsi ini untuk ``ass='...'``.** Nilai yang sudah dikutip
    dibaca berbeda dan justru butuh escape TUNGGAL — lihat
    :func:`quote_filter_path`.
    """
    return str(path).replace("\\", "/").replace(":", "\\\\:")


def quote_filter_path(path: Path) -> str:
    """Lolosikan jalur untuk opsi filter FFmpeg yang **dibungkus kutip tunggal**.

    Dipakai oleh ``ass='...'`` di :func:`burn_subtitles`.

    Kutip tunggal membuat FFmpeg memperlakukan isinya sebagai satu argumen utuh,
    sehingga lapisan pemisah opsi tidak lagi aktif dan titik dua cukup di-escape
    SEKALI.

    **Kesalahan yang pernah terjadi.** Fungsi ini dulu tidak ada, dan
    :func:`escape_filter_path` dipakai untuk keduanya. Ketika escape diubah
    menjadi ganda untuk memperbaiki ``sendcmd``, ``ass='...'`` justru rusak:

        Unable to parse "original_size" option value "/PROJECT/clipper/..."
        as image size

    Dua pemakaian itu memang berbeda; memaksakan satu fungsi untuk keduanya
    selalu merusak salah satunya.
    """
    return str(path).replace("\\", "/").replace(":", "\\:")


def compute_crop_positions(
    path: str | Path,
    info: SourceInfo,
    start_s: float,
    end_s: float,
    *,
    face_model_path: str = "",
    max_frames: int = 0,
    job_id: str | None = None,
) -> tuple[list[int], str]:
    """Hitung posisi x crop untuk setiap frame dalam rentang.

    Mengembalikan daftar posisi (piksel) dan ringkasan kesehatan pelacakan.
    Bila tidak ada wajah sama sekali, daftar kosong dikembalikan supaya pemanggil
    dapat memakai crop tengah statis — jauh lebih dapat diprediksi daripada crop
    yang menempel di posisi awal karena pelacakan tidak pernah menemukan subjek.

    ``path`` adalah video SUMUH dan ``start_s``/``end_s`` adalah waktu absolut
    padanya. Rentang dibaca lewat seek akurat per frame di
    :func:`iter_video_frames`, BUKAN dengan mendekode lalu membuang frame-frame
    awal: untuk video sumber 30 menit, pendekatan lama mendekode seluruh segmen
    sebelum titik mulai hanya untuk membuangnya. ``frame_index`` yang dilaporkan
    ke pelacak wajah diasumsikan relatif terhadap awal segmen (0 = frame
    pertama yang diminta), konsisten dengan asumsi ``sendcmd`` di encode.
    """
    from worker_render.ffmpeg_pipe import iter_video_frames

    crop_w = crop_width_for(info.width, info.height)
    smoother = CropSmoother(
        crop_width=float(crop_w),
        source_width=float(info.width),
        initial_center=info.width / 2.0,
    )

    duration = end_s - start_s if end_s > start_s else 0.0
    last_frame = int(duration * info.fps) if duration > 0 else info.frame_count
    positions: list[int] = []
    frame_index = 0

    with FaceTracker(face_model_path or default_model_path()) as tracker:
        for frame in iter_video_frames(
            path,
            width=info.width,
            height=info.height,
            start_s=start_s,
            duration_s=duration,
            job_id=job_id,
        ):
            if last_frame and frame_index >= last_frame:
                break
            if max_frames and len(positions) >= max_frames:
                break

            faces = tracker.detect(
                frame,
                frame_width=info.width,
                frame_height=info.height,
                frame_index=frame_index,
                fps=info.fps,
            )
            subject = choose_subject(
                faces,
                current_center_x=smoother.smoothed_center,
                source_width=float(info.width),
            )
            smoother.update(subject.center_x if subject else None)
            positions.append(smoother.crop_x())
            frame_index += 1

        health = tracker.tracking_health()
        if not tracker.frames_with_face:
            logger.warning("Pelacakan wajah gagal total: %s", health)
            return [], health

    return positions, health


def write_sendcmd_file(positions: list[int], path: Path, fps: float) -> Path:
    """Tulis berkas perintah posisi crop untuk filter ``sendcmd`` FFmpeg.

    Pendekatan ini dipilih daripada membangun satu ekspresi FFmpeg raksasa:
    berkas perintah mudah dibaca saat debugging, dan panjangnya tidak dibatasi
    batas panjang argumen baris perintah sistem operasi — batas itu nyata untuk
    klip berdurasi menit pada 30 fps (ribuan perintah).
    """
    lines: list[str] = []
    previous = -1
    for index, x in enumerate(positions):
        # Tulis hanya saat nilainya berubah: memperkecil berkas sekaligus
        # membuat perubahan posisi mudah dihitung.
        if x == previous:
            continue
        previous = x
        timestamp = index / max(fps, 1e-6)
        lines.append(f"{timestamp:.4f} crop x {x};")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def build_video_filter(
    info: SourceInfo,
    options: ReframeOptions,
    work: Path,
    *,
    source_path: Path,
    job_id: str | None = None,
) -> tuple[str, str]:
    """Susun rantai filter video sesuai mode.

    ``source_path`` adalah video sumber penuh; pelacakan wajah membacanya pada
    rentang ``options.start_s``..``options.end_s`` lewat seek akurat, jadi tidak
    ada berkas segmen perantara lagi.

    Returns:
        ``(filter_chain, tracking_health)``.
    """
    geometry = plan_letterbox(info.width, info.height, options.output_width, options.output_height)
    out = f"{options.output_width}:{options.output_height}"

    if options.mode is not CropMode.FACE_TRACK:
        # Mode berbar: seluruh bingkai dipertahankan, tidak ada deteksi wajah.
        return build_filter_chain(options.mode, geometry), ""

    # Pelacakan wajah membaca video SUMUH langsung pada rentang segmen (seek
    # akurat per frame), sehingga tidak perlu ada berkas segmen perantara.
    positions, health = compute_crop_positions(
        source_path,
        info,
        options.start_s,
        options.end_s,
        face_model_path=options.face_model_path,
        job_id=job_id,
    )

    if positions:
        commands = write_sendcmd_file(positions, work / _TEMP_COMMANDS, info.fps)
        crop_w = crop_width_for(info.width, info.height)
        # `sendcmd` mengubah nilai `x` filter crop pada waktu tertentu.
        chain = (
            f"sendcmd=f={escape_filter_path(commands)},"
            f"crop={crop_w}:{info.height}:0:0,"
            f"scale={out}"
        )
        return chain, health

    # Tidak ada wajah sama sekali: crop tengah statis. Lebih dapat diprediksi
    # daripada crop yang menempel di posisi awal.
    logger.warning("Tidak ada wajah terdeteksi; memakai crop tengah statis.")
    static_crop = f"crop={crop_width_for(info.width, info.height)}:{info.height}:(in_w-out_w)/2:0,scale={out}"
    return static_crop, health


def render_segment(
    source: str | Path,
    output: str | Path,
    options: ReframeOptions,
    *,
    work_dir: Path | None = None,
    subtitles: Path | None = None,
    fonts_dir: Path | None = None,
    job_id: str | None = None,
) -> dict[str, object]:
    """Render satu segmen menjadi klip vertikal 9:16 dalam SATU pass encode.

    ``subtitles`` (berkas ASS dari :func:`write_subtitles`) dibakar di pass yang
    sama: filter ``ass`` ditambahkan di ujung rantai, jadi tidak ada encode
    kedua hanya untuk subtitle (dan tidak ada generation loss tambahan).
    ``fonts_dir`` berisi font kustom pengguna; libass memuatnya selain font sistem.

    **Mengapa satu pass.** Dahulu jalur ini memotong segmen lebih dulu
    (``cut_segment``, encode ``libx264 ultrafast`` penuh) lalu meng-encode
    ulang seluruhnya pada preset produksi. Pass pertama hanya menghasilkan
    berkas perantara yang langsung dibuang — setiap klip dibayar dua kali.

    Sekarang seek dilakukan sebagai opsi INPUT pada pass produksi itu sendiri.
    Pada jalur DECODE+ENCODE, ``-ss`` input-side bersifat akurat per frame
    (FFmpeg mendekode dari keyframe terdekat dan membuang frame sebelum titik
    yang diminta), jadi ketepatan potongan sama dengan sebelumnya — diverifikasi
    dengan membandingkan frame pertama hasil render terhadap frame sumber tepat
    di ``start_s`` (PSNR ~41 dB, yaitu hanya loss encode x264, bukan pergeseran
    konten). Hal yang sama pada ``-c copy`` TIDAK akurat, karena itulah copy
    tidak dipakai di sini.

    Tahapan tetap dilaporkan terpisah (probe → filter → encode) supaya
    kegagalan satu tahap tidak merusak hasil tahap sebelumnya.
    """
    source_path = Path(source)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    work = work_dir or output_path.parent
    work.mkdir(parents=True, exist_ok=True)

    info = probe_source(source_path, job_id=job_id)
    duration = options.end_s - options.start_s
    if duration <= 0:
        raise ValueError("end_s harus lebih besar dari start_s")

    filter_chain, health = build_video_filter(
        info,
        options,
        work,
        source_path=source_path,
        job_id=job_id,
    )
    if subtitles is not None:
        # quote_filter_path, BUKAN escape_filter_path: nilai ini dibungkus
        # kutip tunggal, dan bentuk itu memerlukan escape titik dua SEKALI.
        ass_filter = f"ass='{quote_filter_path(subtitles)}'"
        if fonts_dir is not None and fonts_dir.is_dir():
            ass_filter += f":fontsdir='{quote_filter_path(fonts_dir)}'"
        filter_chain = f"{filter_chain},{ass_filter}"

    encode_result = run_ffmpeg(
        [
            ffmpeg_executable(), "-y", "-nostdin", "-loglevel", "error",
            # Seek + durasi sebagai opsi INPUT: akurat per frame pada jalur
            # decode (lihat docstring). Ini juga satu-satunya tempat potongan
            # dilakukan, jadi tidak ada encode perantara lagi.
            "-ss", f"{options.start_s:.3f}",
            "-t", f"{duration:.3f}",
            "-i", str(source_path),
            # Hanya trek video pertama + audio bila ada; sumber dengan trek
            # data/subtitle tidak boleh menggagalkan pemetaan.
            "-map", "0:v:0",
            "-map", "0:a?",
            "-vf", filter_chain,
            "-c:v", "libx264",
            "-preset", options.preset,
            "-crf", str(options.crf),
            "-pix_fmt", "yuv420p",
            # Audio dikodekan ulang (bukan copy) agar tetap selaras dengan
            # video yang baru di-encode; bitrate sama dengan yang dipakai
            # jalur lama.
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-threads", str(options.ffmpeg_threads),
            str(output_path),
        ],
        job_id=job_id,
    )
    if not encode_result.ok:
        if subtitles is not None and "No such filter: 'ass'" in encode_result.stderr_tail:
            raise RuntimeError(
                "FFmpeg ini dibangun tanpa libass, sehingga subtitle tidak dapat "
                "dibakar. Pasang build FFmpeg lengkap (scripts\\setup.cmd)."
            )
        raise RuntimeError(f"Encode video gagal: {encode_result.stderr_tail}")

    return {
        "output": str(output_path),
        "duration_s": round(duration, 3),
        "source_width": info.width,
        "source_height": info.height,
        "mode": options.mode.value,
        "encode_seconds": round(encode_result.duration_s, 2),
        "audio_muxed": info.has_audio,
        "tracking_health": health,
    }


def write_subtitles(
    words: list[SubtitleWord],
    work: Path,
    *,
    options: ReframeOptions,
    style: SubtitleStyle | None = None,
) -> Path:
    """Tulis berkas ASS karaoke untuk dibakar oleh :func:`render_segment`.

    ``PlayRes`` diambil dari resolusi **nyata klip**, bukan nilai tetap, karena
    libass meregangkan kanvas yang dideklarasikan agar memenuhi frame — nilai
    tetap membuat subtitle gepeng pada klip non-9:16 (TECH_SPEC §5.2.1).

    Args:
        words: kata bertimestamp, relatif terhadap awal klip.
        work: direktori kerja tempat berkas ditulis.
        options: resolusi keluaran.
        style: gaya pilihan pengguna; ``None`` memakai bawaan modul.
    """
    ass_path = work / _TEMP_SUBS
    ass_path.write_text(
        render_ass(
            words,
            width=options.output_width,
            height=options.output_height,
            style=style or SubtitleStyle(),
        ),
        encoding="utf-8",
    )
    return ass_path
