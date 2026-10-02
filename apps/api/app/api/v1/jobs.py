"""Router jobs: siklus hidup job, segmen, render, dan SSE progres.

Endpoint SSE ``GET /jobs/{id}/stream`` meneruskan event dari worker (thread di
proses ini) lewat ``clipper_shared.worker_events.LocalEventBus``. Riwayat lengkap diambil terpisah dari
``GET /jobs/{id}/events``.

Logika non-HTTP tinggal di :mod:`app.services.jobs`; route di sini hanya
mem-parse request, memanggil service, dan menyusun respons.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from clipper_shared.reframe import CropMode
from clipper_shared.scoring import MAX_SEGMENTS, MIN_SEGMENTS
from clipper_shared.worker_events import TERMINAL_STATUSES, LocalEventBus
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sse_starlette import ServerSentEvent
from sse_starlette.sse import EventSourceResponse

from app.api.v1.auth import CurrentUserOrDev, DbSession
from app.api.v1.youtube import canonical_youtube_url
from app.db.session import SessionLocal
from app.models.job import Job
from app.models.render import Render
from app.models.segment import Segment
from app.services import jobs as jobs_service

router = APIRouter()

#: Heartbeat SSE: menjaga koneksi tetap terbuka saat tahap berjalan lama.
SSE_PING_INTERVAL_S = 15.0


class JobCreateRequest(BaseModel):
    """Payload pembuatan job."""

    source_type: Literal["upload", "youtube"]
    #: Wajib bila source_type='youtube'.
    source_url: str | None = Field(default=None, max_length=2048)
    #: Jumlah klip yang ingin dibuat (1–30, bawaan 5), dibatasi durasi video saat analisis.
    clip_count: int = Field(default=5, ge=MIN_SEGMENTS, le=MAX_SEGMENTS)
    #: Bahasa ucapan video. ``auto`` = deteksi otomatis Whisper.
    language: Literal["id", "en", "auto"] = "id"
    #: Hanya YouTube live: proses N menit terakhir siaran. ``None`` = video biasa.
    live_minutes: int | None = Field(default=None, ge=1, le=600)


class JobResponse(BaseModel):
    """Representasi job untuk klien."""

    id: UUID
    user_id: UUID
    source_type: str
    source_url: str | None
    #: Judul video: nama berkas unggahan atau judul YouTube. ``None`` bila belum
    #: diketahui — job YouTube baru terisi setelah ingest membaca metadata.
    video_title: str | None
    status: str
    stage: str | None
    progress: int
    clip_count: int
    #: Bahasa ucapan: ``id``, ``en``, atau ``auto`` (deteksi otomatis).
    language: str
    live_minutes: int | None
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
        video_title=job.video_title,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        clip_count=job.clip_count,
        language=job.language,
        live_minutes=job.live_minutes,
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


# --- Siklus hidup job --------------------------------------------------------


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(payload: JobCreateRequest, current_user: CurrentUserOrDev, db: DbSession) -> JobResponse:
    """Buat job baru.

    YouTube: URL divalidasi dan disimpan dalam bentuk kanonik, lalu ingest
    langsung dijadwalkan. Unggahan: job menunggu di stage ``upload`` sampai
    ``POST /uploads/{id}/complete``.

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

    job = await jobs_service.create_job(
        db,
        current_user.id,
        source_type=payload.source_type,
        source_url=source_url,
        clip_count=payload.clip_count,
        language=payload.language,
        live_minutes=payload.live_minutes if payload.source_type == "youtube" else None,
    )
    return _to_response(job)


@router.post("/{job_id}/dispatch", response_model=JobResponse, summary="Proses ulang job dari awal")
async def dispatch_job(job_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> JobResponse:
    """Proses ulang job yang gagal/dibatalkan/selesai dari tahap ingest.

    Raises:
        HTTPException: 409 bila job masih berjalan (memprosesnya dua kali
            menghasilkan klip ganda) atau berkas unggahan belum ada.
    """
    job = await jobs_service.dispatch_job(db, current_user.id, job_id)
    return _to_response(job)


@router.post("/{job_id}/rescore", response_model=JobResponse, summary="Jalankan ulang analisis AI")
async def rescore_job(job_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> JobResponse:
    """Ulangi analisis AI memakai transkrip yang sudah ada (tanpa unduh/transkripsi ulang).

    Segmen dan render lama diganti oleh hasil baru.

    Raises:
        HTTPException: 409 job masih berjalan atau belum punya transkrip.
    """
    job = await jobs_service.rescore_job(db, current_user.id, job_id)
    return _to_response(job)


@router.post("/{job_id}/cancel", response_model=JobResponse, summary="Batalkan job")
async def cancel_job(job_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> JobResponse:
    """Batalkan job dan putus proses FFmpeg/yt-dlp/Whisper yang sedang berjalan.

    Status ``canceled`` ditulis lebih dulu, baru proses anak dimatikan: worker
    yang melihat prosesnya mati memeriksa basis data, menemukan ``canceled``,
    lalu berhenti lewat :class:`~clipper_shared.worker_events.JobCanceled` tanpa
    menandai job (atau render) gagal. Whisper lokal diperiksa antar segmen
    (generatornya malas), jadi pembatalan juga berhenti dalam hitungan detik.
    """
    job = await jobs_service.cancel_job(db, current_user.id, job_id)
    return _to_response(job)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Hapus job beserta berkasnya")
async def delete_job(job_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> None:
    """Hapus job, semua barisnya, media mentah, dan hasil render.

    Salinan bernama di ``output/clips/<job_id>`` sengaja TIDAK dihapus: itu
    folder milik pengguna, dan mungkin sudah dipakai di luar aplikasi.
    """
    await jobs_service.delete_job(db, current_user.id, job_id)


@router.get("", response_model=JobListResponse)
async def list_jobs(
    current_user: CurrentUserOrDev,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobListResponse:
    """Daftar job milik user, terbaru dulu."""
    rows, total = await jobs_service.list_jobs(db, current_user.id, limit=limit, offset=offset)
    return JobListResponse(items=[_to_response(job) for job in rows], total=total, limit=limit, offset=offset)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> JobResponse:
    """Detail satu job."""
    return _to_response(await jobs_service.get_owned_job(db, current_user.id, job_id))


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
            job = await jobs_service.get_owned_job(db, current_user.id, job_id)
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
    rows, note = await jobs_service.list_job_segments(db, current_user.id, job_id)
    items = [_segment_to_response(segment) for segment in rows]
    return SegmentListResponse(items=items, total=len(items), note=note)


@router.patch("/{job_id}/segments/{segment_id}", response_model=SegmentResponse, summary="Quick edit segmen")
async def update_segment(
    job_id: UUID, segment_id: UUID, payload: SegmentUpdateRequest, current_user: CurrentUserOrDev, db: DbSession
) -> SegmentResponse:
    """Potong awal/akhir segmen atau tandai dipilih/ditolak.

    Raises:
        HTTPException: 422 rentang tidak sah atau melewati durasi video.
    """
    segment = await jobs_service.update_segment(
        db,
        current_user.id,
        job_id=job_id,
        segment_id=segment_id,
        start_s=payload.start_s,
        end_s=payload.end_s,
        status_value=payload.status,
    )
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
    result = await jobs_service.social_caption(db, current_user.id, job_id, segment_id)
    return SocialCaptionResponse(**result)


# --- Media & log ------------------------------------------------------------


@router.get("/{job_id}/media", response_model=MediaResponse | None, summary="Metadata media sumber")
async def get_job_media(job_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> MediaResponse | None:
    """Dimensi dan durasi media sumber, atau ``null`` bila belum diketahui."""
    media = await jobs_service.get_job_media(db, current_user.id, job_id)
    if media is None:
        return None
    return MediaResponse(
        width=media.width, height=media.height, duration_s=media.duration_s, codec=media.codec, size_bytes=media.size_bytes
    )


@router.get("/{job_id}/media/file", summary="Stream video sumber untuk pemutar di editor")
async def stream_job_media(job_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> FileResponse:
    """Sajikan video sumber. ``FileResponse`` menangani HTTP Range (seek ``<video>``) sendiri."""
    path = await jobs_service.job_media_file(db, current_user.id, job_id)
    return FileResponse(path, media_type="video/mp4")


@router.get("/{job_id}/events", response_model=JobEventListResponse, summary="Log pemrosesan sebuah job")
async def list_job_events(
    job_id: UUID,
    current_user: CurrentUserOrDev,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> JobEventListResponse:
    """Riwayat tahapan job, TERLAMA dulu; ``elapsed_s`` dihitung dari event pertama."""
    items, note = await jobs_service.list_job_events(db, current_user.id, job_id, limit=limit)
    return JobEventListResponse(
        items=[JobEventResponse(**item) for item in items],
        total=len(items),
        note=note,
    )


# --- Render -----------------------------------------------------------------


@router.get("/{job_id}/renders", response_model=RenderListResponse, summary="Daftar render klip untuk sebuah job")
async def list_job_renders(job_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> RenderListResponse:
    """Semua render segmen job ini, terbaru dulu."""
    rows = await jobs_service.list_job_renders(db, current_user.id, job_id)
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
    render_row = await jobs_service.render_job_segment(
        db,
        current_user.id,
        job_id=job_id,
        segment_id=segment_id,
        kind=payload.kind,
        crop_mode=payload.crop_mode,
        preset=payload.preset,
    )
    return _render_to_response(render_row)


@router.get("/{job_id}/renders/{render_id}/file", summary="Stream video hasil render")
async def stream_render_file(job_id: UUID, render_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> FileResponse:
    """Sajikan hasil render (Range ditangani ``FileResponse``)."""
    _render_row, path = await jobs_service.render_file(db, current_user.id, job_id, render_id)
    return FileResponse(path, media_type="video/mp4")


@router.get("/{job_id}/renders/{render_id}/download", summary="Unduh hasil render sebagai MP4")
async def download_render_file(job_id: UUID, render_id: UUID, current_user: CurrentUserOrDev, db: DbSession) -> FileResponse:
    """Unduh berkas klip hasil render."""
    render_row, path = await jobs_service.render_file(db, current_user.id, job_id, render_id)
    name = f"clip_{str(job_id)[:8]}_{str(render_row.segment_id)[:8]}_{render_row.kind}.mp4"
    return FileResponse(path, media_type="video/mp4", filename=name)
