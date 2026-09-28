"""Status job: tulis ke basis data, lalu siarkan ke klien SSE di proses yang sama.

Worker berjalan di thread pool di dalam proses API (lihat
:mod:`clipper_shared.dispatcher`), jadi event cukup dikirim lewat
:class:`LocalEventBus` ke antrean asyncio milik koneksi SSE.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import uuid
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset({"done", "failed", "canceled"})


class JobCanceled(Exception):  # noqa: N818 — ini sinyal, bukan kegagalan
    """Job sudah dibatalkan atau dihapus pengguna; task harus berhenti diam-diam."""


class LocalEventBus:
    """Pub/sub in-process yang aman dipanggil dari thread worker."""

    _subscribers: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]]] = (
        defaultdict(list)
    )
    _lock = threading.Lock()

    @classmethod
    def register(cls, job_id: str) -> asyncio.Queue[dict[str, Any]]:
        """Daftarkan antrean subscriber. Wajib dipanggil dari dalam event loop."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        with cls._lock:
            cls._subscribers[job_id].append((asyncio.get_running_loop(), queue))
        return queue

    @classmethod
    def unregister(cls, job_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Hapus antrean subscriber."""
        with cls._lock:
            entries = cls._subscribers.get(job_id, [])
            entries[:] = [entry for entry in entries if entry[1] is not queue]
            if not entries:
                cls._subscribers.pop(job_id, None)

    @classmethod
    def publish(cls, job_id: str, event: dict[str, Any]) -> None:
        """Kirim event ke semua subscriber job ini."""
        with cls._lock:
            entries = list(cls._subscribers.get(job_id, []))
        for loop, queue in entries:
            with contextlib.suppress(RuntimeError):  # loop sudah ditutup
                loop.call_soon_threadsafe(queue.put_nowait, event)


def emit(job_id: str, status: str, stage: str, progress: int, message: str | None = None) -> None:
    """Perbarui status job, catat di ``job_events``, dan siarkan ke SSE.

    ``jobs.error`` diisi pesan saat ``failed`` dan dikosongkan pada status lain,
    supaya pesan gagal dari percobaan sebelumnya tidak tertinggal.

    Raises:
        JobCanceled: job sudah dibatalkan atau dihapus. Pemanggil (task) harus
            berhenti; menulis status baru akan menimpa pembatalan pengguna.
    """
    from clipper_shared.db import get_db_connection, utc_now

    now = utc_now()
    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT status FROM jobs WHERE id = %s", (job_id,))
        row = cursor.fetchone()
        if row is None or row[0] == "canceled":
            raise JobCanceled(job_id)
        cursor.execute(
            """
            UPDATE jobs
               SET status = %s, stage = %s, progress = %s,
                   error = CASE WHEN %s = 'failed' THEN %s ELSE NULL END,
                   updated_at = %s
             WHERE id = %s
            """,
            (status, stage, progress, status, message, now, job_id),
        )
        cursor.execute(
            "INSERT INTO job_events (id, job_id, stage, message, created_at) VALUES (%s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), job_id, stage, message, now),
        )

    LocalEventBus.publish(
        job_id,
        {
            "job_id": job_id,
            "status": status,
            "stage": stage,
            "progress": progress,
            "message": message,
            "ts": now.isoformat(),
        },
    )


def emit_failed(job_id: str, stage: str, message: str) -> None:
    """Tandai job gagal tanpa pernah melempar exception.

    Dipakai di blok ``except`` task: kegagalan menulis status gagal tidak boleh
    menutupi exception asli.
    """
    try:
        emit(job_id, "failed", stage, 0, message)
    except JobCanceled:
        pass
    except Exception:
        logger.exception("Gagal menulis status gagal job %s", job_id)
