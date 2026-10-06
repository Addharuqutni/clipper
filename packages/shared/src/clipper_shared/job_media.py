"""Di mana berkas sebuah job berada, dan bagaimana menaruh berkas baru di sana.

Satu job satu folder (lihat :mod:`clipper_shared.storage`): video sumber dan
klip hasil render duduk bersebelahan di ``<storage>/<slug-judul>-<id>/``. Modul
ini adalah antarmuka tunggal penyimpanan berkas job bagi API dan worker.

Modul ini **tidak** mengurus baris ``source_media`` — metadata (durasi, ukuran,
codec) milik modul basis data masing-masing pemanggil. Yang ada di sini hanya
berkas fisik dan resolusi key di disk.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path

from clipper_shared import storage as layout
from clipper_shared.db import get_db_connection

logger = logging.getLogger(__name__)

_MEDIA_SUFFIX = re.compile(r"\.(mp4|mkv|mov|webm|avi|m4v|flv|wmv|mpe?g|ts)$", re.IGNORECASE)


class MediaNotFoundError(RuntimeError, FileNotFoundError):
    """Media sumber job tidak ditemukan di disk atau belum selesai diunggah."""


def job_source_key(job_id: str, title: str | None, filename: str) -> str:
    """Object key media unggahan: ``<folder-job>/<nama-aman>``.

    Satu job satu folder, jadi video sumber duduk bersebelahan dengan klip hasilnya.
    """
    label = _MEDIA_SUFFIX.sub("", (title or filename).strip())
    folder = layout.job_folder_name(job_id, label or None)
    return f"{folder}/{layout.safe_filename(filename)}"


def source_key(job_id: str) -> str | None:
    """Object key media sumber job, atau ``None`` bila job belum punya media."""
    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT r2_key FROM source_media WHERE job_id = %s", (job_id,))
        row = cursor.fetchone()
    return str(row[0]) if row and row[0] else None


def job_dir(job_id: str) -> Path:
    """Folder job yang sudah punya media.

    Folder ditentukan oleh ``r2_key`` yang tersimpan (namanya memuat slug judul),
    bukan dihitung ulang dari judul: judul yang berubah belakangan tidak boleh
    memindahkan berkas.

    Raises:
        MediaNotFoundError: job belum punya media sumber.
    """
    key = source_key(job_id)
    if key is None:
        raise MediaNotFoundError(
            f"Job {job_id} belum punya media sumber; tidak ada folder untuk berkasnya."
        )
    folder = layout.job_folder_of_key(key)
    if folder is None:
        folder = str(job_id)
    return layout.job_dir(folder)


def source_path(job_id: str) -> Path:
    """Jalur media sumber job. Dibaca di tempat: tidak ada salinan per klip.

    Raises:
        MediaNotFoundError: job belum punya media atau berkas fisik hilang di disk.
    """
    key = source_key(job_id)
    if key is None:
        raise MediaNotFoundError(f"source_media untuk job {job_id} tidak ditemukan.")
    path = layout.key_path(key)
    if not path.is_file():
        raise MediaNotFoundError(
            f"Media sumber tidak ada di disk ({path}); mungkin sudah dihapus "
            "pembersihan otomatis 48 jam setelah render terakhir."
        )
    return path


def store(job_id: str, filename: str, path: Path, *, title: str | None = None) -> str:
    """Pindahkan sebuah berkas ke folder job; kembalikan object key-nya.

    ``title`` hanya berpengaruh untuk berkas PERTAMA job — yaitu video sumber.
    Untuk berkas berikutnya (klip), folder sudah ada dan judul diabaikan.

    Nama yang sudah terpakai tidak ditimpa: berkas diberi akhiran ``-2``, ``-3``.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Berkas sumber tidak ditemukan: {path}")

    key = source_key(job_id)
    directory = (
        layout.job_dir(layout.job_folder_name(job_id, title))
        if key is None
        else job_dir(job_id)
    )
    target = _unused_path(directory, layout.safe_filename(filename))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), target)
    return f"{target.parent.name}/{target.name}"


def purge(job_id: str) -> bool:
    """Hapus seluruh folder job dan isinya bila ada. Mengembalikan True jika terhapus."""
    try:
        folder = job_dir(job_id)
    except MediaNotFoundError:
        return False

    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
        return True
    return False


def _unused_path(directory: Path, filename: str) -> Path:
    """Jalur di ``directory`` yang belum terpakai untuk ``filename``."""
    target = directory / filename
    if not target.exists():
        return target
    stem, suffix = os.path.splitext(filename)
    counter = 2
    while target.exists():
        target = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return target
