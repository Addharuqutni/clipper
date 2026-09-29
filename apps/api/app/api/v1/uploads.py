"""Router uploads: unggahan berkas lokal per potongan, dengan resume.

Alur (klien: ``apps/web/lib/upload/multipart.ts``)::

    POST /jobs                        {source_type: "upload"}   -> job (stage "upload")
    POST /uploads/init                {job_id, filename, size_bytes}
                                      -> {upload_id, part_size_bytes, part_count, received_parts}
    PUT  /uploads/{upload_id}/parts/{n}   badan = byte potongan ke-n (1-based)
    POST /uploads/{upload_id}/complete    -> gabungkan, catat source_media, mulai ingest
    DELETE /uploads/{upload_id}           -> batalkan, hapus potongan, job dibatalkan

**Resume.** ``/init`` untuk job yang sama dengan ukuran sama mengembalikan
upload yang sudah ada beserta potongan yang sudah diterima, jadi klien hanya
mengirim sisanya — setelah jeda, tab ditutup, atau aplikasi di-restart.

Potongan ditulis langsung ke disk sambil dibaca dari request, tidak pernah
utuh di memori.
"""

from __future__ import annotations

import asyncio
import json
import math
import shutil
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

from clipper_shared import storage as layout
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.v1.auth import CurrentUserOrDev, DbSession
from app.core.config import settings
from app.core.storage import build_raw_key
from app.models.job import Job
from app.models.source_media import SourceMedia
from app.services.jobs import get_owned_job

router = APIRouter()

_MANIFEST = "manifest.json"


class InitUploadRequest(BaseModel):
    """Permintaan memulai (atau melanjutkan) unggahan untuk sebuah job."""

    job_id: UUID
    filename: str = Field(..., min_length=1, max_length=512)
    size_bytes: int = Field(..., gt=0)


class InitUploadResponse(BaseModel):
    """Rencana potongan dan potongan yang sudah diterima server."""

    upload_id: str
    part_size_bytes: int
    part_count: int
    received_parts: list[int]


class CompleteUploadResponse(BaseModel):
    """Hasil penggabungan."""

    job_id: UUID
    status: str


def _upload_dir(upload_id: str) -> Path:
    # upload_id dipakai sebagai nama folder: tolak apa pun selain UUID.
    try:
        return layout.uploads_dir() / str(UUID(upload_id))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload tidak ditemukan") from None


def _read_manifest(upload_id: str) -> dict[str, Any]:
    path = _upload_dir(upload_id) / _MANIFEST
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload tidak ditemukan")
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _received_parts(upload_id: str, manifest: dict[str, Any]) -> list[int]:
    """Nomor potongan yang sudah lengkap di disk (ukuran sesuai rencana)."""
    folder = _upload_dir(upload_id)
    received = []
    for number in range(1, manifest["part_count"] + 1):
        part = folder / f"{number:05d}.part"
        if part.is_file() and part.stat().st_size == _expected_size(manifest, number):
            received.append(number)
    return received


def _expected_size(manifest: dict[str, Any], number: int) -> int:
    part_size, total = int(manifest["part_size_bytes"]), int(manifest["size_bytes"])
    return min(part_size, total - (number - 1) * part_size)




async def _owned_manifest(db: DbSession, user_id: Any, upload_id: str) -> tuple[dict[str, Any], Job]:
    manifest = _read_manifest(upload_id)
    return manifest, await get_owned_job(db, user_id, UUID(manifest["job_id"]))


@router.post("/init", response_model=InitUploadResponse, status_code=status.HTTP_201_CREATED)
async def init_upload(
    payload: InitUploadRequest, current_user: CurrentUserOrDev, db: DbSession
) -> InitUploadResponse:
    """Mulai unggahan, atau lanjutkan unggahan yang sudah ada untuk job ini.

    Raises:
        HTTPException: 404 job tidak ada; 409 job bukan unggahan atau sudah
            punya media; 413 berkas melebihi batas.
    """
    if payload.size_bytes > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Ukuran melebihi batas {settings.MAX_UPLOAD_BYTES / 1024**3:.0f} GB.",
        )
    job = await get_owned_job(db, current_user.id, payload.job_id)
    if job.source_type != "upload":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Job ini bukan job unggahan.")
    has_media = (
        await db.execute(select(SourceMedia.id).where(SourceMedia.job_id == job.id))
    ).first()
    if has_media:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Berkas job ini sudah diunggah.")

    # Resume: upload lama untuk job + ukuran yang sama dipakai ulang.
    root = layout.uploads_dir()
    if root.is_dir():
        for manifest_path in root.glob(f"*/{_MANIFEST}"):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest["job_id"] == str(job.id) and manifest["size_bytes"] == payload.size_bytes:
                upload_id = manifest_path.parent.name
                return InitUploadResponse(
                    upload_id=upload_id,
                    part_size_bytes=manifest["part_size_bytes"],
                    part_count=manifest["part_count"],
                    received_parts=_received_parts(upload_id, manifest),
                )

    upload_id = str(uuid.uuid4())
    manifest = {
        "job_id": str(job.id),
        "filename": payload.filename,
        "size_bytes": payload.size_bytes,
        "part_size_bytes": settings.UPLOAD_PART_BYTES,
        "part_count": math.ceil(payload.size_bytes / settings.UPLOAD_PART_BYTES),
    }
    folder = _upload_dir(upload_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / _MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    return InitUploadResponse(
        upload_id=upload_id,
        part_size_bytes=manifest["part_size_bytes"],
        part_count=manifest["part_count"],
        received_parts=[],
    )


@router.put("/{upload_id}/parts/{part_number}", status_code=status.HTTP_204_NO_CONTENT)
async def put_part(
    upload_id: str, part_number: int, request: Request, current_user: CurrentUserOrDev, db: DbSession
) -> None:
    """Terima satu potongan. Ditulis ke berkas sementara lalu di-rename (atomik).

    Raises:
        HTTPException: 400 nomor potongan di luar rencana; 422 ukuran salah.
    """
    manifest, _job = await _owned_manifest(db, current_user.id, upload_id)
    if not 1 <= part_number <= manifest["part_count"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nomor potongan di luar rencana.")

    expected = _expected_size(manifest, part_number)
    folder = _upload_dir(upload_id)
    temp = folder / f"{part_number:05d}.part.tmp"
    written = 0
    with temp.open("wb") as handle:
        async for chunk in request.stream():
            written += len(chunk)
            if written > expected:
                break
            await asyncio.to_thread(handle.write, chunk)
    if written != expected:
        temp.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Potongan {part_number} berukuran {written} byte, seharusnya {expected}.",
        )
    temp.replace(folder / f"{part_number:05d}.part")


@router.post("/{upload_id}/complete", response_model=CompleteUploadResponse)
async def complete_upload(upload_id: str, current_user: CurrentUserOrDev, db: DbSession) -> CompleteUploadResponse:
    """Gabungkan potongan, catat ``source_media``, lalu jadwalkan ingest.

    Raises:
        HTTPException: 409 masih ada potongan yang belum diterima.
    """
    from app.core.dispatch import dispatch_ingest

    manifest, job = await _owned_manifest(db, current_user.id, upload_id)
    missing = sorted(set(range(1, manifest["part_count"] + 1)) - set(_received_parts(upload_id, manifest)))
    if missing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{len(missing)} potongan belum diterima (mis. #{missing[0]}).",
        )

    key = build_raw_key(str(current_user.id), str(job.id), manifest["filename"])
    target = layout.object_path(layout.RAW, key)
    folder = _upload_dir(upload_id)

    def _assemble() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as out:
            for number in range(1, manifest["part_count"] + 1):
                with (folder / f"{number:05d}.part").open("rb") as part:
                    shutil.copyfileobj(part, out, length=1024 * 1024)
        shutil.rmtree(folder, ignore_errors=True)

    await asyncio.to_thread(_assemble)

    db.add(
        SourceMedia(
            job_id=job.id,
            r2_key=key,
            size_bytes=manifest["size_bytes"],
            original_filename=manifest["filename"],
        )
    )
    job.status = "queued"
    job.stage = "ingest"
    job.error = None
    await db.commit()
    dispatch_ingest(str(job.id), "upload", None)
    return CompleteUploadResponse(job_id=job.id, status="queued")


@router.delete("/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
async def abort_upload(upload_id: str, current_user: CurrentUserOrDev, db: DbSession) -> None:
    """Batalkan unggahan: hapus potongan dan tandai job dibatalkan."""
    _manifest, job = await _owned_manifest(db, current_user.id, upload_id)
    shutil.rmtree(_upload_dir(upload_id), ignore_errors=True)
    if job.stage == "upload":
        job.status = "canceled"
        job.error = "Unggahan dibatalkan."
        await db.commit()
