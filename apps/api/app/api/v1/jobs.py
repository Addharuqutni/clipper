"""Router jobs: siklus hidup job, segmen, render, dan SSE progres.

Endpoint SSE ``GET /jobs/{id}/stream`` meneruskan event dari worker (thread di
proses ini) lewat ``clipper_shared.worker_events.LocalEventBus``. Riwayat lengkap diambil terpisah dari
``GET /jobs/{id}/events``.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from clipper_shared import storage as layout
from clipper_shared.reframe import CropMode
from clipper_shared.scoring import MAX_SEGMENTS, MIN_SEGMENTS
from clipper_shared.worker_events import TERMINAL_STATUSES, LocalEventBus
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sse_starlette import ServerSentEvent
from sse_starlette.sse import EventSourceResponse

from app.api.v1.ai import ai_config_problem
from app.api.v1.auth import CurrentUserOrDev, DbSession, get_owned_job
from app.api.v1.youtube import canonical_youtube_url
from app.db.session import SessionLocal
from app.models.job import Job
from app.models.job_event import JobEvent
from app.models.render import Render
from app.models.segment import Segment
from app.models.source_media import SourceMedia
from app.models.transcript import Transcript

router = APIRouter()

#: Status yang berarti job sedang atau akan diproses — tidak boleh dikirim ulang.
ACTIVE_STATUSES = frozenset({"queued", "running"})

#: Heartbeat SSE: menjaga koneksi tetap terbuka saat tahap berjalan lama.
SSE_PING_INTERVAL_S = 15.0


class JobCreateRequest(BaseModel):
    """Payload pembuatan job."""

    source_type: Literal["upload", "youtube"]
    #: Wajib bila source_type='youtube'.
    source_url: str | None = Field(default=None, max_length=2048)
    #: Jumlah klip yang ingin dibuat (1–30, bawaan 5), dibatasi durasi video saat analisis.
    clip_count: int = Field(default=5, ge=MIN_SEGMENTS, le=MAX_SEGMENTS)


class JobResponse(BaseModel):
    """Representasi job untuk klien."""

    id: UUID
    user_id: UUID
    source_type: str
    source_url: str | None
    status: str
    stage: str | None
    progress: int
    clip_count: int
    error: str | None
    created_at: datetime
    updated_at: datetime


class JobListResponse(BaseModel):
    """Halaman daftar job."""

    items: list[JobResponse]
    total: int
    limit: int
    offset: int


class SegmentResponse(BaseModel):
    """Satu segmen klip yang diusulkan untuk sebuah job."""

    id: UUID
    start_s: float
    end_s: float
    score: float | None
    label: str | None
    hook_score: float | None
    completeness: float | None
    emotional_arc: float | None
    reason: str | None
    status: str


class SegmentListResponse(BaseModel):
    """Daftar segmen beserta ringkasannya."""

    items: list[SegmentResponse]
    total: int
    #: Catatan yang menjelaskan keadaan daftar, mis. bila belum dianalisis.
    note: str = ""


class SegmentUpdateRequest(BaseModel):
    """Quick edit segmen: potong awal/akhir atau pilih/tolak."""

    start_s: float | None = Field(default=None, ge=0)
    end_s: float | None = Field(default=None, gt=0)
    status: Literal["proposed", "selected", "rejected"] | None = None


class JobEventResponse(BaseModel):
    """Satu baris log pemrosesan job."""

    id: UUID
    stage: str
    message: str | None
    #: Detik sejak event PERTAMA — langsung menjawab "tahap mana yang lambat".
    elapsed_s: float
    created_at: datetime


class JobEventListResponse(BaseModel):
    """Log pemrosesan lengkap sebuah job."""

    items: list[JobEventResponse]
    total: int
    note: str = ""


class MediaResponse(BaseModel):
    """Metadata media sumber. Dipakai UI untuk menghitung geometri crop."""

    width: int
    height: int
    duration_s: float | None
    codec: str | None
    size_bytes: int | None


class RenderResponse(BaseModel):
    """Representasi satu hasil atau tugas render."""

    id: UUID
    segment_id: UUID
    kind: str
    r2_key: str | None
    preset: str | None
    status: str
    crop_mode: str
    duration_ms: int | None
    size_bytes: int | None
    created_at: datetime


class RenderListResponse(BaseModel):
    """Daftar render untuk sebuah job."""

    items: list[RenderResponse]
    total: int


class SegmentRenderRequest(BaseModel):
    """Payload pemicu render segmen."""

    kind: Literal["preview", "final"] = "preview"
    crop_mode: CropMode | None = None
    preset: str | None = Field(default=None, max_length=64)


class SocialCaptionResponse(BaseModel):
    """Caption + hashtag siap tempel untuk unggahan klip (FR-4.2)."""

    caption: str
    hashtags: list[str]


def _render_to_response(r: Render) -> RenderResponse:
    return RenderResponse(
        id=r.id,
        segment_id=r.segment_id,
        kind=r.kind,
        r2_key=r.r2_key,
        preset=r.preset,
        status=r.status,
        crop_mode=r.crop_mode,
        duration_ms=r.duration_ms,
        size_bytes=r.size_bytes,
        created_at=r.created_at,
    )


def _to_response(job: Job) -> JobResponse:
    return JobResponse(
        id=job.id,
        user_id=job.user_id,
        source_type=job.source_type,
        source_url=job.source_url,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        clip_count=job.clip_count,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _segment_to_response(segment: Segment) -> SegmentResponse:
    return SegmentResponse(
        id=segment.id,
        start_s=segment.start_s,
        end_s=segment.end_s,
        score=segment.score,
        label=segment.label,
        hook_score=segment.hook_score,
        completeness=segment.completeness,
        emotional_arc=segment.emotional_arc,
        reason=segment.reason,
        status=segment.status,
    )


async def _require_ai_ready(db: DbSession, user_id: Any) -> None:
    """Tolak dengan 422 bila skoring AI pasti gagal, sebelum pekerjaan berat dimulai."""
    problem = await ai_config_problem(db, user_id)
    if problem:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Penyedia AI belum siap: {problem} Isi dulu di halaman Pengaturan.",
        )


async def _get_segment(db: DbSession, job: Job, segment_id: UUID) -> Segment:
    segment = (
        await db.execute(select(Segment).where(Segment.id == segment_id, Segment.job_id == job.id))
    ).scalar_one_or_none()
    if segment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Segmen tidak ditemukan untuk job ini.")
    return segment


def _file_or_404(bucket: str, key: str | None, missing: str) -> Path:
    """Jalur berkas di penyimpanan, atau 404 bila belum/tidak ada."""
    if not key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=missing)
    path = layout.object_path(bucket, key)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=missing)
    return path


# --- Siklus hidup job --------------------------------------------------------


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(payload: JobCreateRequest, current_user: CurrentUserOrDev, db: DbSession) -> JobResponse:
    """Buat job baru.

    YouTube: URL divalidasi dan disimpan dalam bentuk kanonik, lalu ingest
    langsung dijadwalkan. Unggahan: job menunggu di stage ``upload`` sampai
    ``POST /uploads/{id}/complete`` — mengirim ingest sekarang hanya akan gagal
    karena berkasnya belum ada.

    Raises:
        HTTPException: 422 URL tidak sah atau penyedia AI belum siap.
    """
    source_url: str | None = None
    if payload.source_type == "youtube":
        # Bentuk kanonik menutup injeksi opsi yt-dlp (nilai berawalan "-") dan
        # pengambilan URL sembarang lewat extractor generik yt-dlp.
        source_url = canonical_youtube_url(payload.source_url or "")
        if source_url is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="URL YouTube tidak valid. Gunakan youtube.com/watch?v=..., youtu.be/..., atau youtube.com/shorts/...",
            )

    await _require_ai_ready(db, current_user.id)

    job = Job(
        user_id=current_user.id,
        source_type=payload.source_type,
        source_url=source_url,
        status="queued",
        stage="ingest" if payload.source_type == "youtube" else "upload",
        progress=0,
        clip_count=payload.clip_count,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    if payload.source_type == "youtube":
        from app.core.dispatch import dispatch_ingest

        dispatch_ingest(str(job.id), "youtube", source_url)
    return _to_response(job)


@router.post("/{job_id}/dispatch", response_model=JobResponse, summary="Proses ulang job dari awal")
async def dispatch_job(job_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> JobResponse:
    """Proses ulang job yang gagal/dibatalkan/selesai dari tahap ingest.

    Raises:
        HTTPException: 409 bila job masih berjalan (memprosesnya dua kali
            menghasilkan klip ganda) atau berkas unggahan belum ada.
    """
    from app.core.dispatch import dispatch_ingest

    job = await get_owned_job(db, current_user.id, job_id)
    if job.status in ACTIVE_STATUSES and job.stage != "upload":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job sedang diproses (status '{job.status}'). Batalkan dulu bila ingin mengulang.",
        )
    if job.source_type == "upload":
        media = (await db.execute(select(SourceMedia).where(SourceMedia.job_id == job.id))).scalar_one_or_none()
        if media is None or not layout.object_path(layout.RAW, media.r2_key).is_file():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Berkas unggahan tidak ada (belum selesai diunggah atau sudah dihapus). Buat job baru.",
            )

    await _require_ai_ready(db, current_user.id)
    job.status, job.stage, job.progress, job.error = "queued", "ingest", 0, None
    await db.commit()
    await db.refresh(job)
    dispatch_ingest(str(job.id), job.source_type, job.source_url)
    return _to_response(job)


@router.post("/{job_id}/rescore", response_model=JobResponse, summary="Jalankan ulang analisis AI")
async def rescore_job(job_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> JobResponse:
    """Ulangi analisis AI memakai transkrip yang sudah ada (tanpa unduh/transkripsi ulang).

    Segmen dan render lama diganti oleh hasil baru.

    Raises:
        HTTPException: 409 job masih berjalan atau belum punya transkrip.
    """
    from app.core.dispatch import dispatch_rescore

    job = await get_owned_job(db, current_user.id, job_id)
    if job.status in ACTIVE_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Job masih diproses.")
    has_transcript = (await db.execute(select(Transcript.id).where(Transcript.job_id == job.id))).first()
    if not has_transcript:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Job belum punya transkrip untuk dianalisis.")

    await _require_ai_ready(db, current_user.id)
    job.status, job.stage, job.progress, job.error = "queued", "analyze", 60, None
    await db.commit()
    await db.refresh(job)
    dispatch_rescore(str(job.id))
    return _to_response(job)


@router.post("/{job_id}/cancel", response_model=JobResponse, summary="Batalkan job")
async def cancel_job(job_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> JobResponse:
    """Batalkan job. Tahap yang sedang berjalan berhenti di titik periksa berikutnya.

    Proses FFmpeg/Whisper yang sudah mulai tidak diputus paksa; hasilnya
    dibuang dan tahap berikutnya tidak dijadwalkan.
    """
    job = await get_owned_job(db, current_user.id, job_id)
    if job.status in ACTIVE_STATUSES:
        job.status, job.error = "canceled", "Dibatalkan pengguna."
        segment_ids = select(Segment.id).where(Segment.job_id == job.id)
        await db.execute(
            update(Render)
            .where(Render.segment_id.in_(segment_ids), Render.status.in_(["queued", "running"]))
            .values(status="failed")
        )
        await db.commit()
        await db.refresh(job)
    return _to_response(job)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Hapus job beserta berkasnya")
async def delete_job(job_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> None:
    """Hapus job, semua barisnya, media mentah, dan hasil render.

    Salinan bernama di ``output/clips`` sengaja TIDAK dihapus: itu folder milik
    pengguna, dan mungkin sudah dipakai di luar aplikasi.
    """
    job = await get_owned_job(db, current_user.id, job_id)
    media = (await db.execute(select(SourceMedia).where(SourceMedia.job_id == job.id))).scalar_one_or_none()
    segment_ids = [row[0] for row in (await db.execute(select(Segment.id).where(Segment.job_id == job.id))).all()]

    await db.execute(delete(Job).where(Job.id == job.id))
    await db.commit()

    def _remove_files() -> None:
        if media is not None and media.r2_key:
            shutil.rmtree(layout.object_path(layout.RAW, media.r2_key).parent, ignore_errors=True)
        for segment_id in segment_ids:
            shutil.rmtree(layout.storage_root() / layout.RENDERS / "renders" / str(segment_id), ignore_errors=True)

    await asyncio.to_thread(_remove_files)


@router.get("", response_model=JobListResponse)
async def list_jobs(
    current_user: CurrentUserOrDev,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobListResponse:
    """Daftar job milik user, terbaru dulu."""
    total = (
        await db.execute(select(func.count()).select_from(Job).where(Job.user_id == current_user.id))
    ).scalar_one()
    rows = (
        await db.execute(
            select(Job)
            .where(Job.user_id == current_user.id)
            .order_by(Job.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return JobListResponse(items=[_to_response(job) for job in rows], total=total, limit=limit, offset=offset)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> JobResponse:
    """Detail satu job."""
    return _to_response(await get_owned_job(db, current_user.id, job_id))


@router.get("/{job_id}/stream")
async def stream_job(job_id: UUID, current_user: CurrentUserOrDev) -> EventSourceResponse:
    """SSE progres job.

    Kontrak:

    * ``event: status`` — pembaruan stage/progress (JSON job_id/status/stage/progress/message).
    * ``event: end`` — job mencapai status terminal; server menutup stream.
    * ``: ping`` — heartbeat tiap :data:`SSE_PING_INTERVAL_S` detik.

    Tidak memakai dependency ``DbSession``: sesi dari dependency baru ditutup
    setelah respons selesai, sehingga satu tab yang terbuka menahan satu
    koneksi basis data selama berjam-jam. Langganan didaftarkan SEBELUM
    snapshot dibaca, supaya event yang terjadi di antaranya tidak hilang.
    """
    queue = LocalEventBus.register(str(job_id))
    try:
        async with SessionLocal() as db:
            job = await get_owned_job(db, current_user.id, job_id)
            snapshot: dict[str, Any] = {
                "job_id": str(job.id),
                "status": job.status,
                "stage": job.stage,
                "progress": job.progress,
                "message": job.error,
            }
    except BaseException:
        LocalEventBus.unregister(str(job_id), queue)
        raise

    async def event_generator() -> AsyncIterator[bytes]:
        try:
            payload = snapshot
            while True:
                if payload.get("type") == "ping":
                    yield ServerSentEvent(comment="ping").encode()
                else:
                    name = "end" if payload.get("status") in TERMINAL_STATUSES else "status"
                    yield ServerSentEvent(event=name, data=json.dumps(payload, default=str)).encode()
                    if name == "end":
                        return
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=SSE_PING_INTERVAL_S)
                except TimeoutError:
                    payload = {"type": "ping"}
        finally:
            LocalEventBus.unregister(str(job_id), queue)

    return EventSourceResponse(event_generator())


# --- Segmen -----------------------------------------------------------------


@router.get("/{job_id}/segments", response_model=SegmentListResponse, summary="Daftar segmen klip untuk sebuah job")
async def list_job_segments(job_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> SegmentListResponse:
    """Segmen terurut skor tertinggi. ``note`` menjelaskan MENGAPA daftar kosong."""
    job = await get_owned_job(db, current_user.id, job_id)
    rows = (
        await db.execute(
            select(Segment).where(Segment.job_id == job.id).order_by(Segment.score.desc(), Segment.start_s.asc())
        )
    ).scalars().all()
    items = [_segment_to_response(segment) for segment in rows]

    note = ""
    if not items:
        if job.status == "failed":
            note = job.error or "Job gagal sebelum analisis menghasilkan segmen."
        elif job.status in ACTIVE_STATUSES:
            note = f"Job masih pada tahap '{job.stage}'. Segmen muncul setelah transkripsi dan analisis selesai."
        else:
            note = "Analisis selesai tetapi tidak ada segmen tersimpan. Klik 'Analisis ulang' untuk mencoba lagi."
    return SegmentListResponse(items=items, total=len(items), note=note)


@router.patch("/{job_id}/segments/{segment_id}", response_model=SegmentResponse, summary="Quick edit segmen")
async def update_segment(
    job_id: UUID, segment_id: UUID, payload: SegmentUpdateRequest, current_user: CurrentUserOrDev, db: DbSession
) -> SegmentResponse:
    """Potong awal/akhir segmen atau tandai dipilih/ditolak.

    Raises:
        HTTPException: 422 rentang tidak sah atau melewati durasi video.
    """
    job = await get_owned_job(db, current_user.id, job_id)
    segment = await _get_segment(db, job, segment_id)
    start_s = segment.start_s if payload.start_s is None else payload.start_s
    end_s = segment.end_s if payload.end_s is None else payload.end_s
    if end_s - start_s < 1:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Durasi segmen minimal 1 detik.")
    media = (await db.execute(select(SourceMedia).where(SourceMedia.job_id == job.id))).scalar_one_or_none()
    if media is not None and media.duration_s and end_s > media.duration_s + 0.5:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Akhir segmen melewati durasi video ({media.duration_s:.1f} detik).",
        )
    segment.start_s, segment.end_s = start_s, end_s
    if payload.status is not None:
        segment.status = payload.status
    await db.commit()
    await db.refresh(segment)
    return _segment_to_response(segment)


@router.post(
    "/{job_id}/segments/{segment_id}/social-caption",
    response_model=SocialCaptionResponse,
    summary="Buat caption + hashtag untuk unggahan klip (AI)",
)
async def social_caption(job_id: UUID, segment_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> SocialCaptionResponse:
    """Minta penyedia AI menulis caption dan hashtag dari transkrip klip (PRD FR-4.2).

    Raises:
        HTTPException: 422 transkrip kosong atau penyedia AI gagal.
    """
    from worker_light.social_caption import CaptionError, generate_social_caption

    job = await get_owned_job(db, current_user.id, job_id)
    segment = await _get_segment(db, job, segment_id)
    try:
        result = await asyncio.to_thread(
            generate_social_caption, str(job.id), segment.start_s, segment.end_s, segment.label or ""
        )
    except CaptionError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return SocialCaptionResponse(**result)


# --- Media & log ------------------------------------------------------------


@router.get("/{job_id}/media", response_model=MediaResponse | None, summary="Metadata media sumber")
async def get_job_media(job_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> MediaResponse | None:
    """Dimensi dan durasi media sumber, atau ``null`` bila belum diketahui."""
    job = await get_owned_job(db, current_user.id, job_id)
    media = (await db.execute(select(SourceMedia).where(SourceMedia.job_id == job.id))).scalar_one_or_none()
    if media is None or not media.width or not media.height:
        return None
    return MediaResponse(
        width=media.width, height=media.height, duration_s=media.duration_s, codec=media.codec, size_bytes=media.size_bytes
    )


@router.get("/{job_id}/media/file", summary="Stream video sumber untuk pemutar di editor")
async def stream_job_media(job_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> FileResponse:
    """Sajikan video sumber. ``FileResponse`` menangani HTTP Range (seek ``<video>``) sendiri."""
    job = await get_owned_job(db, current_user.id, job_id)
    media = (await db.execute(select(SourceMedia).where(SourceMedia.job_id == job.id))).scalar_one_or_none()
    path = _file_or_404(layout.RAW, media.r2_key if media else None, "Media sumber belum/tidak lagi tersedia.")
    return FileResponse(path, media_type="video/mp4")


@router.get("/{job_id}/events", response_model=JobEventListResponse, summary="Log pemrosesan sebuah job")
async def list_job_events(
    job_id: UUID,
    current_user: CurrentUserOrDev,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> JobEventListResponse:
    """Riwayat tahapan job, TERLAMA dulu; ``elapsed_s`` dihitung dari event pertama."""
    job = await get_owned_job(db, current_user.id, job_id)
    rows = (
        await db.execute(
            select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.created_at.asc()).limit(limit)
        )
    ).scalars().all()
    base = rows[0].created_at if rows else job.created_at
    items = [
        JobEventResponse(
            id=row.id,
            stage=row.stage,
            message=row.message,
            elapsed_s=max(0.0, (row.created_at - base).total_seconds()),
            created_at=row.created_at,
        )
        for row in rows
    ]
    note = ""
    if not rows:
        note = (
            f"Belum ada log. Job masih pada tahap '{job.stage}'."
            if job.status in ACTIVE_STATUSES
            else "Tidak ada log tersimpan untuk job ini."
        )
    return JobEventListResponse(items=items, total=len(items), note=note)


# --- Render -----------------------------------------------------------------


@router.get("/{job_id}/renders", response_model=RenderListResponse, summary="Daftar render klip untuk sebuah job")
async def list_job_renders(job_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> RenderListResponse:
    """Semua render segmen job ini, terbaru dulu."""
    job = await get_owned_job(db, current_user.id, job_id)
    rows = (
        await db.execute(
            select(Render)
            .join(Segment, Segment.id == Render.segment_id)
            .where(Segment.job_id == job.id)
            .order_by(Render.created_at.desc())
        )
    ).scalars().all()
    return RenderListResponse(items=[_render_to_response(r) for r in rows], total=len(rows))


@router.post(
    "/{job_id}/segments/{segment_id}/render",
    response_model=RenderResponse,
    summary="Picu render untuk satu segmen (pratinjau atau final)",
)
async def render_job_segment(
    job_id: UUID,
    segment_id: UUID,
    payload: SegmentRenderRequest,
    current_user: CurrentUserOrDev,
    db: DbSession,
) -> RenderResponse:
    """Jadwalkan render segmen. Render aktif dengan kind yang sama dikembalikan apa adanya.

    Raises:
        HTTPException: 409 media sumber sudah dihapus pembersihan otomatis.
    """
    from app.core.dispatch import dispatch_render

    job = await get_owned_job(db, current_user.id, job_id)
    segment = await _get_segment(db, job, segment_id)
    media = (await db.execute(select(SourceMedia).where(SourceMedia.job_id == job.id))).scalar_one_or_none()
    if media is None or not layout.object_path(layout.RAW, media.r2_key).is_file():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Media sumber sudah tidak ada (dihapus 48 jam setelah render terakhir). Buat job baru.",
        )

    existing = (
        await db.execute(
            select(Render)
            .where(Render.segment_id == segment.id, Render.kind == payload.kind, Render.status.in_(["queued", "running"]))
            .order_by(Render.created_at.desc())
        )
    ).scalars().first()
    if existing is not None:
        return _render_to_response(existing)

    crop_mode = (payload.crop_mode or CropMode.FACE_TRACK).value
    render_row = Render(segment_id=segment.id, kind=payload.kind, crop_mode=crop_mode, status="queued", preset=payload.preset)
    db.add(render_row)
    # Render baru: jangan hapus media mentah di tengah jalan; worker menetapkan
    # ulang batas 48 jam setelah render terakhir selesai.
    media.expires_at = None
    job.status, job.stage, job.error = "running", "render", None
    await db.commit()
    await db.refresh(render_row)

    dispatch_render(str(job.id), str(segment.id), payload.kind, payload.preset, crop_mode)
    return _render_to_response(render_row)


async def _get_render(db: DbSession, job: Job, render_id: UUID) -> Render:
    render_row = (
        await db.execute(
            select(Render).join(Segment, Segment.id == Render.segment_id).where(Render.id == render_id, Segment.job_id == job.id)
        )
    ).scalar_one_or_none()
    if render_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Render tidak ditemukan.")
    return render_row


@router.get("/{job_id}/renders/{render_id}/file", summary="Stream video hasil render")
async def stream_render_file(job_id: UUID, render_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> FileResponse:
    """Sajikan hasil render (Range ditangani ``FileResponse``)."""
    job = await get_owned_job(db, current_user.id, job_id)
    render_row = await _get_render(db, job, render_id)
    path = _file_or_404(layout.RENDERS, render_row.r2_key, "Berkas render belum tersedia.")
    return FileResponse(path, media_type="video/mp4")


@router.get("/{job_id}/renders/{render_id}/download", summary="Unduh hasil render sebagai MP4")
async def download_render_file(job_id: UUID, render_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> FileResponse:
    """Unduh berkas klip hasil render."""
    job = await get_owned_job(db, current_user.id, job_id)
    render_row = await _get_render(db, job, render_id)
    path = _file_or_404(layout.RENDERS, render_row.r2_key, "Berkas render belum tersedia.")
    name = f"clip_{str(job_id)[:8]}_{str(render_row.segment_id)[:8]}_{render_row.kind}.mp4"
    return FileResponse(path, media_type="video/mp4", filename=name)
