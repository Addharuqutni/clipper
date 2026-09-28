"""Proses anak per job: registrasi, penantian, dan pembatalan yang memutus pohon proses.

Di mode Standalone (satu proses: API + worker di thread pool) penjadwalan dan
pembatalan berada di proses yang sama, jadi pembatalan dapat langsung mematikan
proses anak yang sedang berjalan:

* **Windows** — ``taskkill /T /F /PID`` mematikan **pohon** proses. Ini bukan
  kemewahan: yt-dlp menjalankan FFmpeg untuk menggabungkan video+audio, sehingga
  mematikan yt-dlp saja meninggalkan FFmpeg yatim yang terus menulis ke disk.
* **POSIX** — ``Popen.kill()`` pada proses langsung. Mode VPS/Celery belum aktif
  (T9), jadi belum ada dukungan process group.

Semua peluncuran proses dari worker WAJIB lewat :func:`run_process`/:func:`spawn`
supaya terdaftar di sini. Tanpa registrasi, pembatalan hanya terlihat pada titik
periksa ``emit`` berikutnya — utang teknis yang ditutup modul ini.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Any

from clipper_shared.worker_events import JobCanceled, is_canceled

logger = logging.getLogger(__name__)

#: Interval pemeriksaan pembatalan selama menunggu proses anak. Cukup cepat
#: untuk pembatalan yang terasa seketika, cukup jarang untuk tidak membebani
#: SQLite di jalur panjang (encode berjalan menit-menitan).
POLL_INTERVAL_S = 0.5

_lock = threading.Lock()
#: Proses anak yang sedang berjalan, per job.
_running: dict[str, set[subprocess.Popen[Any]]] = {}


def register(job_id: str, process: subprocess.Popen[Any]) -> None:
    """Daftarkan proses anak milik sebuah job."""
    with _lock:
        _running.setdefault(job_id, set()).add(process)


def unregister(job_id: str, process: subprocess.Popen[Any]) -> None:
    """Hapus proses dari daftar (dipanggil di blok ``finally`` pemanggil)."""
    with _lock:
        processes = _running.get(job_id)
        if processes is not None:
            processes.discard(process)
            if not processes:
                _running.pop(job_id, None)


def registered_pids(job_id: str) -> list[int]:
    """PID proses anak yang terdaftar untuk sebuah job (diagnostik/test)."""
    with _lock:
        return sorted(process.pid for process in _running.get(job_id, ()))


def kill_process_tree(process: subprocess.Popen[Any]) -> None:
    """Hentikan proses dan seluruh anaknya.

    Di Windows ``taskkill /T`` menelusuri pohon proses; ``Popen.kill`` dipanggil
    sebagai jaring pengaman (mis. bila ``taskkill`` tidak tersedia). Di POSIX
    hanya proses langsung yang dimatikan.
    """
    if process.poll() is not None:
        return
    if os.name == "nt":
        taskkill = shutil.which("taskkill") or "taskkill"
        subprocess.run(  # noqa: S603
            [taskkill, "/T", "/F", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
    if process.poll() is None:
        with contextlib.suppress(OSError):
            process.kill()
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        process.wait(timeout=10)


def terminate_job(job_id: str) -> int:
    """Hentikan semua proses anak job ini. Mengembalikan jumlah proses.

    Dipanggil API saat pembatalan, **setelah** status ``canceled`` tersimpan:
    worker yang melihat prosesnya mati memeriksa basis data dan ikut berhenti
    dengan :class:`JobCanceled`, bukan menandai job gagal.
    """
    with _lock:
        processes = list(_running.get(job_id, ()))
    for process in processes:
        try:
            kill_process_tree(process)
        except Exception:  # noqa: BLE001 — kegagalan satu proses tidak menghentikan sisanya
            logger.exception("Gagal mematikan proses %s milik job %s", process.pid, job_id)
    if processes:
        logger.info("Pembatalan job %s: %d proses anak dihentikan.", job_id, len(processes))
    return len(processes)


def _next_slice(
    deadline: float | None,
    poll_s: float,
    process: subprocess.Popen[Any],
    timeout_s: float | None,
) -> float:
    """Jatah tunggu berikutnya; mematikan proses dan melempar bila batas waktu lewat."""
    if deadline is None:
        return poll_s
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        kill_process_tree(process)
        raise subprocess.TimeoutExpired(process.args, timeout_s or 0.0)
    return min(poll_s, remaining)


def spawn(command: list[str], *, job_id: str | None = None, **kwargs: Any) -> subprocess.Popen[Any]:
    """Jalankan proses anak, terdaftar untuk pembatalan bila ``job_id`` diberikan.

    Raises:
        JobCanceled: job sudah dibatalkan sebelum proses sempat dimulai.
    """
    if job_id is not None and is_canceled(job_id):
        raise JobCanceled(job_id)
    process = subprocess.Popen(command, **kwargs)  # noqa: S603
    if job_id is not None:
        register(job_id, process)
    return process


def wait_with_cancel(process: subprocess.Popen[Any], *, job_id: str | None = None, timeout_s: float | None = None) -> int:
    """Tunggu proses selesai; mematikan pohon proses bila job dibatalkan.

    Raises:
        JobCanceled: job dibatalkan saat proses berjalan.
        subprocess.TimeoutExpired: ``timeout_s`` terlampaui (proses sudah mati).
    """
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    while True:
        slice_s = _next_slice(deadline, POLL_INTERVAL_S, process, timeout_s)
        try:
            return process.wait(timeout=slice_s)
        except subprocess.TimeoutExpired:
            pass
        if job_id is not None and is_canceled(job_id):
            kill_process_tree(process)
            raise JobCanceled(job_id) from None


def run_process(
    command: list[str],
    *,
    job_id: str | None = None,
    timeout_s: float | None = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """Jalankan proses sekali jalan dengan keluaran ditangkap.

    Menerima ``subprocess.run``-style ``capture_output=True, text=True`` (dan
    menerjemahkannya ke ``Popen``); sisanya diteruskan apa adanya.

    Raises:
        JobCanceled: job dibatalkan sebelum/di tengah proses.
        subprocess.TimeoutExpired: ``timeout_s`` terlampaui (proses sudah mati).
    """
    if kwargs.pop("capture_output", False):
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)
    encoding = kwargs.pop("encoding", None)
    text = kwargs.pop("text", False) or encoding is not None
    # `check` TIDAK boleh dibuang diam-diam: pemanggil yang bergantung padanya
    # (perilaku subprocess.run) harus tetap mendapat CalledProcessError.
    check = bool(kwargs.pop("check", False))

    process = spawn(command, job_id=job_id, text=text, encoding=encoding, **kwargs)
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    try:
        while True:
            slice_s = _next_slice(deadline, POLL_INTERVAL_S, process, timeout_s)
            try:
                stdout, stderr = process.communicate(timeout=slice_s)
                break
            except subprocess.TimeoutExpired:
                pass
            if job_id is not None and is_canceled(job_id):
                kill_process_tree(process)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.communicate(timeout=5)
                raise JobCanceled(job_id) from None
    finally:
        if job_id is not None:
            unregister(job_id, process)

    # Urutan penting: pembatalan diperiksa LEBIH DULU. Proses yang mati karena
    # dibatalkan bukan kegagalan, jadi tidak boleh muncul sebagai
    # CalledProcessError yang lalu diformat menjadi pesan "Gagal ...".
    if process.returncode != 0 and job_id is not None and is_canceled(job_id):
        raise JobCanceled(job_id)
    if check and process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, command, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
