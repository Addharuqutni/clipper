"""Penulisan berkas dari API: media unggahan, font, dan aset overlay.

Tata letak folder ada di :mod:`clipper_shared.storage`, dipakai bersama worker.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from clipper_shared import storage as layout

#: Karakter yang dibuang dari nama berkas sebelum dipakai sebagai object key.
#: Nama berkas datang dari klien dan dapat berisi pemisah jalur atau ``..``.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_part(value: str) -> str:
    """Bersihkan satu komponen object key dari vektor path traversal."""
    cleaned = _UNSAFE.sub("_", value).replace("..", "_")
    return cleaned.strip("._") or "file"


def build_raw_key(user_id: str, job_id: str, filename: str) -> str:
    """Object key media unggahan: ``raw/<user_id>/<job_id>/<nama-aman>``."""
    return f"raw/{_safe_part(user_id)}/{_safe_part(job_id)}/{_safe_part(filename)}"


def _unique_key(prefix: str, user_id: str, filename: str) -> str:
    """Key unik per unggahan: dua berkas bernama ``image.png`` tidak saling timpa."""
    suffix = Path(_safe_part(filename)).suffix.lower()
    return f"{prefix}/{_safe_part(user_id)}/{uuid.uuid4().hex}{suffix}"


def _write(bucket: str, key: str, content: bytes) -> Path:
    target = layout.object_path(bucket, key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def store_font_bytes(user_id: str, filename: str, content: bytes) -> tuple[str, Path]:
    """Simpan berkas font; kembalikan ``(object_key, jalur)``."""
    key = _unique_key("fonts", user_id, filename)
    return key, _write(layout.FONTS, key, content)


def store_overlay_bytes(user_id: str, filename: str, content: bytes) -> tuple[str, Path]:
    """Simpan aset B-roll/efek suara; kembalikan ``(object_key, jalur)``."""
    key = _unique_key("overlays", user_id, filename)
    return key, _write(layout.OVERLAYS, key, content)


def delete_object(bucket: str, key: str | None) -> None:
    """Hapus satu berkas bila ada (idempoten)."""
    if key:
        layout.object_path(bucket, key).unlink(missing_ok=True)
