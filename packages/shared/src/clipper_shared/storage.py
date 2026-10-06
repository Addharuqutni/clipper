"""Tata letak penyimpanan di disk, dipakai bersama API dan worker.

Semua berkas berada di bawah ``LOCAL_STORAGE_DIR`` (bawaan ``<repo>/output``)::

    <folder-job>/<berkas>        video sumber + klip hasil render
    clipper-overlays/overlays/.. aset B-roll / efek suara
    clipper-fonts/fonts/...      font kustom
    uploads/<upload_id>/<n>      potongan unggahan yang belum selesai
    secrets/...                  cookies YouTube terenkripsi
    clipper.db                   basis data SQLite

**Satu job, satu folder.** Video sumber dan semua klipnya berada di folder yang
sama di akar penyimpanan, bernama ``<slug-judul>-<8 huruf pertama id job>``
(:func:`job_folder_name`), sehingga hasil sebuah job bisa dibuka langsung dari
Explorer tanpa menelusuri folder perantara. Object key yang tersimpan di kolom
``r2_key`` karena itu **relatif terhadap akar penyimpanan**, bukan terhadap
sebuah bucket: ``SHOWKESMAS-TEPE-IBOT-99644f5b/source.mp4``.

Nama folder dipilih sekali saat berkas pertama job disimpan dan disimpan di
dalam key, jadi judul yang berubah belakangan tidak memindahkan berkas.

**Kunci gaya lama.** Versi sebelumnya menyebar media ke bucket ``clipper-raw``
dan ``clipper-renders`` dengan key berawalan ``raw/`` dan ``renders/``. Kunci
seperti itu masih dibaca lewat :func:`key_path` supaya basis data yang belum
dimigrasi tetap berfungsi. Folder ``clipper-overlays`` dan ``clipper-fonts``
masih dipakai apa adanya: keduanya aset bersama antar job, bukan media job.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

RAW = "clipper-raw"
RENDERS = "clipper-renders"
OVERLAYS = "clipper-overlays"
FONTS = "clipper-fonts"

#: Bucket gaya lama beserta awalan object key-nya. Dipakai :func:`key_path`
#: untuk membaca baris basis data yang belum dimigrasi.
LEGACY_BUCKETS = {"raw": RAW, "renders": RENDERS, "overlays": OVERLAYS, "fonts": FONTS}

#: Panjang maksimum nama folder job, dihitung dari batas jalur Windows (260).
MAX_JOB_FOLDER_LEN = 80

#: Nama perangkat lama Windows yang tidak dapat dipakai sebagai nama berkas.
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")
_JOB_FOLDER = re.compile(r"[a-z0-9][a-z0-9-]*")

def repo_root() -> Path:
    """Akar repositori (packages/shared/src/clipper_shared -> naik 4 tingkat)."""
    return Path(__file__).resolve().parents[4]

def storage_root() -> Path:
    """Akar penyimpanan. Jalur relatif ditafsirkan terhadap akar repo."""
    root = Path(os.getenv("LOCAL_STORAGE_DIR") or "output")
    return root if root.is_absolute() else repo_root() / root

def _parts(key: str) -> list[str]:
    """Komponen ``key`` yang aman dipakai sebagai komponen jalur.

    Komponen ``..`` dan kosong dibuang: ``key`` berasal dari basis data, dan
    tanpa pembersihan sebuah nilai dapat menunjuk ke luar akar penyimpanan.
    """
    return [part for part in key.replace("\\", "/").split("/") if part not in {"", ".", ".."}]

def object_path(bucket: str, key: str) -> Path:
    """Petakan ``(bucket, key)`` ke jalur berkas di dalam ``bucket``.

    Hanya dipakai untuk aset bersama (overlay, font). Media job memakai
    :func:`key_path`.
    """
    parts = _parts(key)
    if not parts:
        raise ValueError(f"object key kosong: {key!r}")
    return storage_root() / bucket / Path(*parts)

def slugify(value: str, *, limit: int = 60) -> str:
    """Jadikan teks bebas (judul video, label segmen) satu komponen jalur aman.

    Huruf kecil, hanya ``a-z0-9-``, tanpa tanda hubung di ujung. Hasilnya aman
    di Windows maupun Linux dan tidak dapat berisi pemisah jalur.
    """
    slug = _SLUG_UNSAFE.sub("-", str(value).lower()).strip("-")
    if len(slug) <= limit:
        return slug
    cut = slug[:limit]
    if slug[limit] != "-":
        # Potong di batas kata supaya nama folder tidak berakhir di tengah kata
        # (``...-gaada-be``), kecuali memang tidak ada batas kata sama sekali.
        cut = cut.rsplit("-", 1)[0] if "-" in cut else cut
    return cut.rstrip("-")

def safe_filename(value: str, *, limit: int = 120) -> str:
    """Nama berkas aman dari sebuah nama, tanpa mengubah nama yang sudah aman.

    Huruf besar-kecil, ``_``, ``-``, dan ``.`` dipertahankan apa adanya: nama
    klip hasil render sudah dibentuk kode dan harus tetap sama persis. Yang
    diubah hanya karakter di luar ``[A-Za-z0-9._-]`` — spasi, pemisah jalur, dan
    ``..`` — serta nama perangkat Windows (``CON``, ``NUL``, ...) yang tidak
    dapat dibuat di Windows.

    Pemanggil yang butuh nama rapi untuk dibaca manusia memakai :func:`slugify`
    lebih dulu; fungsi ini menjaga nama yang sudah benar, bukan mempercantiknya.
    """
    name = Path(str(value).replace("\\", "/")).name
    stem, _, suffix = name.rpartition(".")
    if not stem:
        # Tanpa titik sama sekali (``rpartition`` mengembalikan batang kosong).
        stem, suffix = name, ""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "-", stem).replace("..", "_").strip("._-")
    if not cleaned:
        cleaned = "berkas"
    if cleaned[:limit].upper() in RESERVED_NAMES:
        cleaned = f"file-{cleaned}"
    extension = re.sub(r"[^A-Za-z0-9]", "", suffix)[:8].lower()
    stem = cleaned[:limit].rstrip("._-") or "berkas"
    return f"{stem}.{extension}" if extension else stem

def job_folder_name(job_id: str, title: str | None) -> str:
    """Nama folder job: ``<slug-judul>-<8 huruf pertama id>``.

    Akhiran id menjamin folder tetap unik dan dapat ditelusuri balik ke baris
    ``jobs`` walau judulnya kosong, sama, atau berubah. Akhiran itu juga membuat
    nama folder tidak pernah sama dengan nama bucket gaya lama.
    """
    short = re.sub(r"[^A-Za-z0-9]", "", str(job_id))[:8] or "job"
    slug = slugify(title or "", limit=MAX_JOB_FOLDER_LEN - len(short) - 1)
    return f"{slug}-{short}" if slug else short

def job_dir(folder: str) -> Path:
    """Jalur folder job di akar penyimpanan.

    Raises:
        ValueError: ``folder`` bukan satu komponen jalur yang sah. Nama folder
            berasal dari basis data; tanpa pemeriksaan ini nilai yang rusak
            dapat menunjuk ke luar akar penyimpanan.
    """
    if not _JOB_FOLDER.fullmatch(folder):
        raise ValueError(f"Nama folder job tidak valid: {folder!r}")
    return storage_root() / folder

def job_folder_of_key(key: str | None) -> str | None:
    """Nama folder job dari object key gaya baru, atau ``None`` bila gaya lama."""
    head = str(key or "").replace("\\", "/").partition("/")[0]
    if not head or head in LEGACY_BUCKETS:
        return None
    return head if _JOB_FOLDER.fullmatch(head) else None

def is_legacy_key(key: str | None) -> bool:
    """Apakah key masih memakai tata letak lama (``raw/...``, ``renders/...``)."""
    head = str(key or "").replace("\\", "/").partition("/")[0]
    return head in LEGACY_BUCKETS

def key_path(key: str) -> Path:
    """Jalur berkas dari object key, gaya baru maupun gaya lama.

    Gaya baru relatif terhadap akar penyimpanan; gaya lama dipetakan ke bucket
    lamanya sehingga basis data yang belum dimigrasi tetap dapat dibaca.
    """
    if is_legacy_key(key):
        return object_path(LEGACY_BUCKETS[key.replace("\\", "/").partition("/")[0]], key)
    parts = _parts(key)
    if not parts:
        raise ValueError(f"object key kosong: {key!r}")
    return storage_root() / Path(*parts)

def uploads_dir() -> Path:
    """Direktori potongan unggahan yang belum selesai."""
    return storage_root() / "uploads"

def youtube_cookies_path() -> Path:
    """Cookies YouTube terenkripsi milik pengguna (satu pengguna, satu berkas)."""
    return storage_root() / "secrets" / "youtube_cookies.enc"

def repo_path(env_name: str, default_relative: str) -> Path:
    """Jalur dari env ``env_name``, atau ``<repo>/<default_relative>``.

    Jalur relatif (di env maupun bawaan) ditafsirkan terhadap akar repo, bukan
    direktori kerja proses — uvicorn dijalankan dari ``apps/api``.
    """
    path = Path(os.getenv(env_name) or default_relative)
    return path if path.is_absolute() else repo_root() / path

def binary(name: str) -> str:
    """Jalur program eksternal (``ffmpeg``, ``ffprobe``).

    Urutan: env ``<NAME>_BINARY`` → ``<repo>/.libs/ffmpeg/<name>.exe`` (dipasang
    ``start.cmd``) → ``PATH``. Bila tidak ada sama sekali, nama polos
    dikembalikan supaya pesan gagalnya datang dari sistem operasi.
    """
    import shutil

    override = os.getenv(f"{name.upper()}_BINARY")
    if override:
        return override
    bundled = repo_root() / ".libs" / "ffmpeg" / f"{name}.exe"
    if bundled.is_file():
        return str(bundled)
    return shutil.which(name) or name


# --- Aset non-job (Font & Overlay) ------------------------------------------

FONTS = "fonts"
OVERLAYS = "overlays"

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_part(value: str) -> str:
    cleaned = _UNSAFE_CHARS.sub("_", value).replace("..", "_")
    return cleaned.strip("._") or "file"


def _unique_key(prefix: str, user_id: str, filename: str) -> str:
    suffix = Path(_safe_part(filename)).suffix.lower()
    return f"{prefix}/{_safe_part(user_id)}/{uuid.uuid4().hex}{suffix}"


def _write_bytes(bucket: str, key: str, content: bytes) -> Path:
    target = object_path(bucket, key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def store_font_bytes(user_id: str, filename: str, content: bytes) -> tuple[str, Path]:
    """Simpan berkas font ke penyimpanan global; kembalikan ``(object_key, jalur)``."""
    key = _unique_key(FONTS, user_id, filename)
    return key, _write_bytes(FONTS, key, content)


def store_overlay_bytes(user_id: str, filename: str, content: bytes) -> tuple[str, Path]:
    """Simpan aset B-roll/efek suara ke penyimpanan global; kembalikan ``(object_key, jalur)``."""
    key = _unique_key(OVERLAYS, user_id, filename)
    return key, _write_bytes(OVERLAYS, key, content)


def delete_object(bucket: str, key: str | None) -> None:
    """Hapus satu berkas bila ada (idempoten)."""
    if key:
        object_path(bucket, key).unlink(missing_ok=True)
