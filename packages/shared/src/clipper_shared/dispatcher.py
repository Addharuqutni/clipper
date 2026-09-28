"""Menjalankan tahap pipeline di thread pool di dalam proses API.

Satu pool per jenis pekerjaan, dan **ukuran pool adalah batas paralelnya**:

* ``ingest`` — unduh/probe dan panggilan LLM; lebih banyak menunggu jaringan.
* ``stt`` — Whisper, memakan seluruh core; ``STT_SLOTS`` (bawaan 1).
* ``render`` — FFmpeg + MediaPipe; ``RENDER_SLOTS`` (bawaan 1).

Dengan pool terpisah, antrean render yang panjang tidak menahan job baru di
tahap ingest, dan tidak ada semaphore atau retry-loop yang perlu dijaga:
pekerjaan yang belum dapat giliran cukup menunggu di antrean pool-nya.
"""

from __future__ import annotations

import importlib
import logging
import os
from concurrent.futures import ThreadPoolExecutor
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
    """
    module_path, func_name = task_name.rsplit(".", 1)
    try:
        getattr(importlib.import_module(module_path), func_name)(*args)
    except Exception:
        logger.exception("Task '%s' gagal", task_name)


def submit(task_name: str, *args: Any) -> None:
    """Jadwalkan task di pool-nya.

    Raises:
        KeyError: nama task tidak terdaftar di :data:`TASK_POOLS`.
    """
    pool = TASK_POOLS[task_name]
    logger.info("Menjadwalkan %s%s di pool '%s'", task_name, tuple(args), pool)
    _executor(pool).submit(_run, task_name, list(args))


def shutdown() -> None:
    """Batalkan pekerjaan yang belum mulai saat API berhenti."""
    for executor in _executors.values():
        executor.shutdown(wait=False, cancel_futures=True)
    _executors.clear()
