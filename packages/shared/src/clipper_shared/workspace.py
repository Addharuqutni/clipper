"""Direktori kerja sementara untuk tahap pipeline.

Bawaan ``<repo>/.work`` (dapat ditimpa ``WORKER_WORKSPACE_DIR``): video panjang
butuh beberapa GB ruang sementara, dan folder temp sistem sering berada di
drive C: yang sempit.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

#: Ruang kosong minimum agar pekerjaan dengan video panjang tidak gagal di
#: tengah jalan. Video 60 menit pada 1080p dapat mencapai ~2 GB, dan
#: penggabungan butuh ruang untuk berkas sementara sekaligus hasilnya.
MIN_FREE_BYTES = 4 * 1024**3


def workspace_root() -> Path:
    """Direktori induk semua ruang kerja sementara (dibuat bila belum ada)."""
    from clipper_shared.storage import repo_path

    root = repo_path("WORKER_WORKSPACE_DIR", ".work")
    root.mkdir(parents=True, exist_ok=True)
    _warn_if_too_small(root)
    return root


def _warn_if_too_small(path: Path) -> None:
    """Catat peringatan bila ruang kosong tidak cukup untuk video panjang.

    Peringatan, bukan error: pekerjaan dengan klip pendek tetap dapat berjalan,
    dan menolak semua pekerjaan karena ruang yang kurang akan lebih merugikan
    daripada membiarkan pengguna mencoba dan melihat kegagalan yang jelas.
    """
    try:
        free = shutil.disk_usage(path).free
    except OSError:
        return

    if free < MIN_FREE_BYTES:
        logger.warning(
            "Ruang kerja '%s' hanya menyisakan %.1f GB (disarankan >= %.0f GB). "
            "Video panjang dapat gagal dengan 'No space left on device'. "
            "Setel WORKER_WORKSPACE_DIR ke direktori pada disk yang lebih besar.",
            path,
            free / 1024**3,
            MIN_FREE_BYTES / 1024**3,
        )


def make_workspace(prefix: str) -> Path:
    """Buat ruang kerja unik untuk satu tugas.

    Mengembalikan direktori yang belum ada; pemanggil bertanggung jawab
    menghapusnya (biasanya lewat ``shutil.rmtree(..., ignore_errors=True)`` di
    blok ``finally``).
    """
    return Path(tempfile.mkdtemp(prefix=prefix, dir=workspace_root()))
