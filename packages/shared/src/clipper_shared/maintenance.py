"""Pemeliharaan yang dijalankan API saat startup dan berkala.

* :func:`reconcile_interrupted_jobs` — pekerjaan berjalan di thread proses API,
  jadi menutup jendela, ``stop.cmd``, atau reboot membunuhnya tanpa jejak. Saat
  startup, job/render yang masih ``queued``/``running`` pasti yatim.
* :func:`purge_expired_raw_media` — hapus media mentah 48 jam setelah render
  terakhir job selesai (TECH_SPEC §3, keputusan T6).
* :func:`sweep_workspaces` — hapus ruang kerja sementara yang tertinggal.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timedelta

from clipper_shared import storage as layout
from clipper_shared.db import get_db_connection, to_db_timestamp, utc_now
from clipper_shared.job_state import ACTIVE_STATUSES, FAILED, UPLOAD

logger = logging.getLogger(__name__)

INTERRUPTED_MESSAGE = "Terhenti karena aplikasi ditutup saat job berjalan. Klik 'Proses ulang'."


def raw_media_expiry() -> datetime:
    """Waktu kedaluwarsa media mentah bila render terakhir selesai sekarang."""
    return utc_now() + timedelta(hours=int(os.getenv("RAW_MEDIA_TTL_HOURS", "48")))


def reconcile_interrupted_jobs() -> int:
    """Tandai job dan render yatim sebagai gagal. WAJIB dipanggil sebelum task baru jalan.

    Tanpa ini job macet ``running`` selamanya: endpoint proses ulang menolak job
    berjalan, render aktif yang basi menghalangi render ulang, dan edit
    transkrip ditolak.

    Returns:
        Jumlah job yang ditandai gagal.
    """
    now = utc_now()
    # Jumlah placeholder diturunkan dari ACTIVE_STATUSES supaya menambah status
    # aktif tidak perlu mengedit SQL ini. Yang disisipkan hanya rangkaian "%s";
    # nilainya tetap parameter, jadi tidak ada masukan pengguna di dalam query.
    active = sorted(ACTIVE_STATUSES)
    placeholders = ", ".join("%s" for _ in active)
    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE renders SET status = %s WHERE status IN ({placeholders})",  # noqa: S608
            (FAILED, *active),
        )
        cursor.execute(
            f"""
            UPDATE jobs
               SET status = %s, error = %s, progress = 0, updated_at = %s
             WHERE status IN ({placeholders})
               -- Job yang masih menunggu unggahan tidak terhenti: unggahan
               -- dapat dilanjutkan setelah restart (resume).
               AND COALESCE(stage, '') <> %s
            """,  # noqa: S608
            (FAILED, INTERRUPTED_MESSAGE, now, *active, UPLOAD),
        )
        count = cursor.rowcount
    if count:
        logger.warning("%d job terhenti saat aplikasi ditutup; ditandai gagal.", count)
    return count


def purge_expired_raw_media() -> int:
    """Hapus media mentah yang lewat ``source_media.expires_at``.

    Baris ``source_media`` dipertahankan (metadata tetap berguna untuk UI);
    hanya berkasnya yang dihapus dan ``expires_at`` dikosongkan agar tidak
    diproses dua kali.

    Folder job TIDAK dihapus walau menjadi kosong: di dalamnya ada klip hasil
    render, dan sisanya akan terisi lagi bila job dirender ulang.

    Returns:
        Jumlah berkas yang dihapus.
    """
    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, r2_key FROM source_media WHERE expires_at IS NOT NULL AND expires_at < %s",
            (to_db_timestamp(utc_now()),),
        )
        rows = cursor.fetchall()

    deleted = 0
    for media_id, r2_key in rows:
        try:
            layout.key_path(r2_key).unlink(missing_ok=True)
            deleted += 1
        except (OSError, ValueError) as exc:
            logger.warning("Gagal menghapus media mentah %s: %s", r2_key, exc)
            continue
        with get_db_connection() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE source_media SET expires_at = NULL WHERE id = %s", (media_id,))
    if deleted:
        logger.info("Retensi: %d media mentah kedaluwarsa dihapus.", deleted)
    return deleted


#: Awalan folder yang dibuat task (lihat ``make_workspace`` di tasks). Hanya ini
#: yang disapu: WORKER_WORKSPACE_DIR bisa saja menunjuk folder milik pengguna.
WORKSPACE_PREFIXES = ("ingest-", "stt-", "render-")


def sweep_workspaces() -> None:
    """Hapus ruang kerja sementara sisa sesi lalu. Hanya aman saat tidak ada task berjalan."""
    from clipper_shared.workspace import workspace_root

    for child in workspace_root().iterdir():
        if child.is_dir() and child.name.startswith(WORKSPACE_PREFIXES):
            shutil.rmtree(child, ignore_errors=True)
