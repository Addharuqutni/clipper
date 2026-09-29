"""Eksekusi FFmpeg yang tidak macet.

FFmpeg menulis log ke ``stderr``. Bila tidak ada yang membacanya, buffer pipa
(sekitar 64 KB di Windows) penuh dan FFmpeg berhenti bekerja tanpa pesan apa
pun — proses menggantung selamanya. :func:`run_ffmpeg` membaca ``stderr`` terus
di thread terpisah (menyimpan ekornya untuk pesan gagal), dan
:func:`iter_video_frames` membaca frame mentah dari ``stdout`` untuk pelacakan
wajah.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from clipper_shared.processes import (
    POLL_INTERVAL_S,
    kill_process_tree,
    spawn,
    unregister,
    wait_with_cancel,
)
from clipper_shared.storage import binary
from clipper_shared.worker_events import JobCanceled, is_canceled

logger = logging.getLogger(__name__)


#: Batas ukuran log stderr yang disimpan di memori. Tanpa batas, encoding video
#: panjang bisa menghabiskan memori hanya untuk menyimpan log.
MAX_STDERR_BYTES = 64 * 1024


def ffmpeg_executable() -> str:
    """Jalur FFmpeg (lihat :func:`clipper_shared.storage.binary`)."""
    return binary("ffmpeg")


def ffprobe_executable() -> str:
    """Jalur ffprobe (lihat :func:`clipper_shared.storage.binary`)."""
    return binary("ffprobe")


@dataclass
class EncodeResult:
    """Hasil satu eksekusi FFmpeg."""

    returncode: int
    frames_written: int
    stderr_tail: str = ""
    stalled: bool = False
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.stalled


@dataclass
class _StderrCollector:
    """Pembaca stderr yang berjalan di thread terpisah.

    Menyimpan hanya ekor log (dibatasi ukuran) karena yang dibutuhkan untuk
    diagnostik adalah bagian akhir, bukan awal.
    """

    #: ``IO[bytes]``, bukan ``BinaryIO``: typeshed modern memberi tipe ini untuk
    #: ``Popen.stderr``, dan ``BinaryIO`` menyebabkan ketidakcocokan di setiap
    #: pemanggil. ``readline`` tersedia di keduanya.
    stream: IO[bytes]
    chunks: list[bytes] = field(default_factory=list)
    total: int = 0
    _thread: threading.Thread | None = None
    _stop: threading.Event = field(default_factory=threading.Event)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="ffmpeg-stderr", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            for line in iter(self.stream.readline, b""):
                self.total += len(line)
                self.chunks.append(line)
                # Pangkas dari depan begitu melewati batas: yang berguna adalah
                # pesan kesalahan terakhir sebelum proses mati.
                while sum(len(chunk) for chunk in self.chunks) > MAX_STDERR_BYTES and len(self.chunks) > 1:
                    self.chunks.pop(0)
        except (ValueError, OSError):
            # Pipa ditutup saat proses dimatikan — bukan kondisi error.
            pass

    def join(self, timeout: float = 5.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    @property
    def text(self) -> str:
        return b"".join(self.chunks).decode(errors="replace")


def run_ffmpeg(
    command: list[str],
    *,
    timeout_s: float | None = None,
    job_id: str | None = None,
) -> EncodeResult:
    """Jalankan FFmpeg sekali jalan tanpa masukan frame dari memori.

    Dipakai untuk tahap yang hanya membaca/menulis berkas (potong segmen,
    mux audio, burn subtitle). Untuk tahap ini, thread pembuangan stderr tetap
    dipakai karena argumen yang salah dapat membuat FFmpeg menulis ribuan baris
    log lalu memblokir.

    ``job_id`` mendaftarkan proses ini agar pembatalan job memutusnya seketika
    (lihat :mod:`clipper_shared.processes`).

    Raises:
        JobCanceled: job dibatalkan saat encode berjalan; proses sudah mati.
    """
    started = time.monotonic()
    process = spawn(
        command,
        job_id=job_id,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    try:
        if process.stderr is None:
            process.kill()
            raise RuntimeError("Gagal menyiapkan pipa stderr FFmpeg.")

        collector = _StderrCollector(stream=process.stderr)
        collector.start()

        try:
            returncode = wait_with_cancel(process, job_id=job_id, timeout_s=timeout_s)
        except subprocess.TimeoutExpired:
            kill_process_tree(process)
            returncode = process.wait()
            logger.error("FFmpeg melewati batas waktu %.0f detik dan dimatikan.", timeout_s or 0)
        finally:
            collector.join()

        process.stderr.close()

        # Proses bisa mati karena dimatikan API (balapan dengan poll di
        # wait_with_cancel): jangan laporkan sebagai kegagalan encode.
        if returncode != 0 and job_id is not None and is_canceled(job_id):
            raise JobCanceled(job_id)
    finally:
        if job_id is not None:
            unregister(job_id, process)

    return EncodeResult(
        returncode=returncode,
        frames_written=0,
        stderr_tail=collector.text[-4000:],
        duration_s=time.monotonic() - started,
    )


def iter_video_frames(
    path: str | Path,
    *,
    width: int,
    height: int,
    start_s: float = 0.0,
    duration_s: float = 0.0,
    job_id: str | None = None,
) -> Iterator[bytes]:
    """Baca frame video sebagai BGR mentah dari ``stdout`` FFmpeg.

    Dipakai untuk tahap yang perlu memeriksa setiap frame (pelacakan wajah).
    Membaca dari FFmpeg, bukan dari OpenCV, karena FFmpeg jauh lebih cepat
    dalam mendekode dan kita sudah memerlukannya untuk encoding.

    ``start_s``/``duration_s`` membatasi rentang yang dibaca. Keduanya diberikan
    sebagai opsi INPUT (``-ss``/``-t`` sebelum ``-i``) — pada jalur DECODE ini
    seek bersifat akurat per frame: FFmpeg menemukan keyframe terdekat sebelum
    ``start_s``, mendekode darinya, lalu membuang frame sampai tepat di
    ``start_s``. Hal yang sama pada jalur ``-c copy`` TIDAK akurat (hanya bisa
    mulai di keyframe), jadi jangan pakai parameter ini untuk copy.

    ``job_id`` mendaftarkan proses ini agar pembatalan job memutusnya; pembacaan
    frame juga memeriksa pembatalan paling sering sekali per ``POLL_INTERVAL_S``.

    Raises:
        JobCanceled: job dibatalkan saat pembacaan frame berlangsung.
    """
    frame_bytes = width * height * 3
    args: list[str] = [
        ffmpeg_executable(),
        "-nostdin",
        "-loglevel", "error",
        # Batasi thread DECODER juga (TECH_SPEC §4.3); -threads setelah -i
        # hanya membatasi encoder.
        "-threads", os.getenv("FFMPEG_THREADS", "2"),
    ]
    if start_s > 0.0:
        args += ["-ss", f"{start_s:.3f}"]
    if duration_s > 0.0:
        args += ["-t", f"{duration_s:.3f}"]
    args += [
        "-i", str(path),
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-",
    ]

    process = spawn(
        args,
        job_id=job_id,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=frame_bytes * 2,
    )

    try:
        if process.stdout is None:
            process.kill()
            raise RuntimeError("Gagal menyiapkan pipa stdout FFmpeg untuk pembacaan frame.")

        next_check = time.monotonic() + POLL_INTERVAL_S
        while True:
            frame = process.stdout.read(frame_bytes)
            if len(frame) < frame_bytes:
                break
            # Pelacakan wajah membaca ribuan frame dalam sekali panggilan;
            # tanpa pemeriksaan di sini pembatalan baru terlihat setelah
            # seluruh segmen selesai dilacak. Dibatasi per waktu: memeriksa tiap
            # frame berarti satu koneksi SQLite per frame (±1.800 per menit video).
            if job_id is not None and time.monotonic() >= next_check:
                if is_canceled(job_id):
                    raise JobCanceled(job_id)
                next_check = time.monotonic() + POLL_INTERVAL_S
            yield frame
        # Proses bisa mati karena dimatikan API (balapan dengan poll di atas);
        # sebagian frame tidak boleh dianggap sebagai hasil yang sah.
        if job_id is not None and is_canceled(job_id):
            raise JobCanceled(job_id)
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if process.poll() is None:
            kill_process_tree(process)
        with contextlib.suppress(Exception):
            process.wait()
        if job_id is not None:
            unregister(job_id, process)
