"""Menjalankan tahap pipeline di thread pool di dalam proses API.

Satu pool per jenis pekerjaan, dan **ukuran pool adalah batas paralelnya**:

* ``ingest`` — unduh/probe dan panggilan LLM; lebih banyak menunggu jaringan.
* ``stt`` — Whisper, memakan seluruh core; ``STT_SLOTS`` (bawaan 1).
* ``render`` — FFmpeg + MediaPipe; ``RENDER_SLOTS`` (bawaan 1).

Dengan pool terpisah, antrean render yang panjang tidak menahan job baru di
tahap ingest: pekerjaan yang belum dapat giliran cukup menunggu di antrean
pool-nya.

**Transkripsi didahulukan.** Render yang belum mulai menunggu selama ada
transkripsi yang antre atau berjalan (:func:`wait_for_stt_idle`), karena
keduanya berebut core yang sama. Render yang sudah berjalan tidak disela.
"""

from __future__ import annotations

import importlib
import logging
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

logger = logging.getLogger(__name__)

#: Nama task -> pool. Nama berupa string supaya API tidak perlu mengimpor
#: modul worker (dan dependensi beratnya) saat startup.
TASK_POOLS: dict[str, str] = {
    "worker_light.tasks.ingest_media": "ingest",
    "worker_light.tasks.score_segments": "ingest",
    "worker_light.tasks.transcribe_media": "stt",
    "worker_render.tasks.render_clip": "render",
}

_POOL_SIZE_ENV = {"ingest": ("INGEST_WORKERS", "2"), "stt": ("STT_SLOTS", "1"), "render": ("RENDER_SLOTS", "1")}

_executors: dict[str, ThreadPoolExecutor] = {}

#: Transkripsi didahulukan dari render: keduanya memakan seluruh core, dan
#: berjalan bersamaan memperlambat keduanya. Hitungan task ``stt`` yang antre
#: atau berjalan; render yang BELUM mulai menunggu sampai nol. Render yang
#: sudah berjalan dibiarkan selesai — menghentikannya membuang hasil encode.
_stt_active = 0
_stt_idle = threading.Condition()
#: Diset ``shutdown()``: render yang menunggu dilepas agar proses bisa berhenti.
_shutting_down = False

#: Interval pemeriksaan pembatalan job selama render menunggu transkripsi.
STT_WAIT_POLL_S = 2.0


def _executor(pool: str) -> ThreadPoolExecutor:
    """Ambil (atau buat) executor untuk satu pool."""
    if pool not in _executors:
        env, default = _POOL_SIZE_ENV[pool]
        _executors[pool] = ThreadPoolExecutor(
            max_workers=max(1, int(os.getenv(env, default))),
            thread_name_prefix=f"clipper-{pool}",
        )
    return _executors[pool]


def _run(task_name: str, args: list[Any]) -> None:
    """Impor lalu jalankan fungsi task. Exception dicatat, tidak dilempar ulang.

    Task sendiri yang menulis status ``failed`` ke basis data; di sini hanya
    jaring pengaman supaya exception tidak hilang tanpa jejak di dalam Future.
    Render menunggu di sini, SEBELUM ``render_clip`` menandai barisnya
    ``running``, sehingga di UI render yang menunggu tetap ``queued``.
    """
    if TASK_POOLS[task_name] == "render":
        wait_for_stt_idle(str(args[0]) if args else None)
    module_path, func_name = task_name.rsplit(".", 1)
    try:
        getattr(importlib.import_module(module_path), func_name)(*args)
    except Exception:
        logger.exception("Task '%s' gagal", task_name)


def _stt_finished(_future: Future[None]) -> None:
    """Kurangi hitungan transkripsi. Dipanggil juga saat future DIBATALKAN
    (``shutdown(cancel_futures=True)``), yang tidak pernah menjalankan ``_run``."""
    global _stt_active
    with _stt_idle:
        _stt_active = max(0, _stt_active - 1)
        _stt_idle.notify_all()


def wait_for_stt_idle(job_id: str | None = None) -> None:
    """Blok sampai tidak ada transkripsi yang antre atau berjalan.

    Berhenti menunggu lebih awal bila ``job_id`` dibatalkan (task render
    sendiri lalu keluar lewat ``JobCanceled``) atau dispatcher dimatikan.
    """
    from clipper_shared.worker_events import is_canceled

    logged = False
    while True:
        with _stt_idle:
            if _stt_active <= 0 or _shutting_down:
                return
            if not logged:
                logger.info("Render job %s menunggu %d transkripsi selesai", job_id, _stt_active)
                logged = True
            _stt_idle.wait(STT_WAIT_POLL_S)
            waiting = _stt_active > 0 and not _shutting_down
        # Di luar lock: is_canceled membuka koneksi SQLite, dan submit/
        # _stt_finished di thread lain tidak boleh tertahan selama kueri.
        if waiting and job_id is not None and is_canceled(job_id):
            return


def submit(task_name: str, *args: Any) -> None:
    """Jadwalkan task di pool-nya.

    Raises:
        KeyError: nama task tidak terdaftar di :data:`TASK_POOLS`.
    """
    global _stt_active
    pool = TASK_POOLS[task_name]
    logger.info("Menjadwalkan %s%s di pool '%s'", task_name, tuple(args), pool)
    if pool != "stt":
        _executor(pool).submit(_run, task_name, list(args))
        return
    # Dihitung saat DIJADWALKAN, bukan saat mulai: render yang mulai di sela
    # antrean pool stt tetap harus mengalah.
    with _stt_idle:
        _stt_active += 1
    try:
        future = _executor(pool).submit(_run, task_name, list(args))
    except BaseException:
        _stt_finished(Future())
        raise
    future.add_done_callback(_stt_finished)


def shutdown() -> None:
    """Batalkan pekerjaan yang belum mulai saat API berhenti."""
    global _shutting_down
    with _stt_idle:
        _shutting_down = True
        _stt_idle.notify_all()
    for executor in _executors.values():
        executor.shutdown(wait=False, cancel_futures=True)
    _executors.clear()


def reset_for_tests() -> None:
    """Kembalikan dispatcher ke keadaan awal (hanya untuk test)."""
    global _shutting_down, _stt_active
    shutdown()
    with _stt_idle:
        _shutting_down = False
        _stt_active = 0
