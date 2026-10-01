"""Service job: siklus hidup job, segmen, media, log, dan render.

Route di :mod:`app.api.v1.jobs` menyusun respons HTTP dari kembalian service di
sini; kesalahan domain (:class:`~app.services.errors.ServiceError`) dipetakan
handler global di ``app.main`` ke respons yang sama seperti sebelumnya.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from clipper_shared import storage as layout
from clipper_shared.reframe import CropMode
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.job_event import JobEvent
from app.models.render import Render
from app.models.segment import Segment
from app.models.source_media import SourceMedia
from app.services.ai_settings import ai_config_problem
from app.services.errors import ConflictError, NotFoundError, UnprocessableError

#: Status yang berarti job sedang atau akan diproses — tidak boleh dikirim ulang.
ACTIVE_STATUSES = frozenset({"queued", "running"})


# --- Kepemilikan & pemeriksaan bersama --------------------------------------


async def get_owned_job(db: AsyncSession, user_id: Any, job_id: UUID) -> Job:
    """Ambil job milik pengguna, atau 404."""
    job = (
        await db.execute(select(Job).where(Job.id == job_id, Job.user_id == user_id))
    ).scalar_one_or_none()
    if job is None:
        raise NotFoundError("Job tidak ditemukan")
    return job


async def require_ai_ready(db: AsyncSession, user_id: Any) -> None:
    """Tolak dengan 422 bila skoring AI pasti gagal, sebelum pekerjaan berat dimulai."""
    problem = await ai_config_problem(db, user_id)
    if problem:
        raise UnprocessableError(
            f"Penyedia AI belum siap: {problem} Isi dulu di halaman Pengaturan."
        )


async def get_segment(db: AsyncSession, job: Job, segment_id: UUID) -> Segment:
    """Segmen milik job ini, atau 404."""
    segment = (
        await db.execute(select(Segment).where(Segment.id == segment_id, Segment.job_id == job.id))
    ).scalar_one_or_none()
    if segment is None:
        raise NotFoundError("Segmen tidak ditemukan untuk job ini.")
    return segment


def file_or_404(bucket: str, key: str | None, missing: str) -> Path:
    """Jalur berkas di penyimpanan, atau 404 bila belum/tidak ada."""
    if not key:
        raise NotFoundError(missing)
    path = layout.object_path(bucket, key)
    if not path.is_file():
        raise NotFoundError(missing)
    return path


async def get_render(db: AsyncSession, job: Job, render_id: UUID) -> Render:
    """Render milik salah satu segmen job ini, atau 404."""
    render_row = (
        await db.execute(
            select(Render)
            .join(Segment, Segment.id == Render.segment_id)
            .where(Render.id == render_id, Segment.job_id == job.id)
        )
    ).scalar_one_or_none()
    if render_row is None:
        raise NotFoundError("Render tidak ditemukan.")
    return render_row


# --- Siklus hidup job -------------------------------------------------------


async def create_job(
    db: AsyncSession,
    user_id: Any,
    *,
    source_type: str,
    source_url: str | None,
    clip_count: int,
    language: str = "id",
) -> Job:
    """Buat job baru; jadwalkan ingest untuk YouTube.

    Unggahan: job menunggu di stage ``upload`` sampai
    ``POST /uploads/{id}/complete`` — mengirim ingest sekarang hanya akan gagal
    karena berkasnya belum ada.
    """
    await require_ai_ready(db, user_id)

    job = Job(
        user_id=user_id,
        source_type=source_type,
        source_url=source_url,
        status="queued",
        stage="ingest" if source_type == "youtube" else "upload",
        progress=0,
        clip_count=clip_count,
        language=language,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    if source_type == "youtube":
        from app.core.dispatch import dispatch_ingest

        dispatch_ingest(str(job.id), "youtube", source_url)
    return job


async def dispatch_job(db: AsyncSession, user_id: Any, job_id: UUID) -> Job:
    """Proses ulang job yang gagal/dibatalkan/selesai dari tahap ingest.

    Raises:
        ConflictError: job masih berjalan (memprosesnya dua kali menghasilkan
            klip ganda) atau berkas unggahan belum ada.
    """
    from app.core.dispatch import dispatch_ingest

    job = await get_owned_job(db, user_id, job_id)
    if job.status in ACTIVE_STATUSES and job.stage != "upload":
        raise ConflictError(
            f"Job sedang diproses (status '{job.status}'). Batalkan dulu bila ingin mengulang."
        )
    if job.source_type == "upload":
        media = (await db.execute(select(SourceMedia).where(SourceMedia.job_id == job.id))).scalar_one_or_none()
        if media is None or not layout.object_path(layout.RAW, media.r2_key).is_file():
            raise ConflictError(
                "Berkas unggahan tidak ada (belum selesai diunggah atau sudah dihapus). Buat job baru."
            )

    await require_ai_ready(db, user_id)
    job.status, job.stage, job.progress, job.error = "queued", "ingest", 0, None
    await db.commit()
    await db.refresh(job)
    dispatch_ingest(str(job.id), job.source_type, job.source_url)
    return job


async def rescore_job(db: AsyncSession, user_id: Any, job_id: UUID) -> Job:
    """Ulangi analisis AI memakai transkrip yang sudah ada.

    Raises:
        ConflictError: job masih berjalan atau belum punya transkrip.
    """
    from app.core.dispatch import dispatch_rescore
    from app.models.transcript import Transcript

    job = await get_owned_job(db, user_id, job_id)
    if job.status in ACTIVE_STATUSES:
        raise ConflictError("Job masih diproses.")
    has_transcript = (await db.execute(select(Transcript.id).where(Transcript.job_id == job.id))).first()
    if not has_transcript:
        raise ConflictError("Job belum punya transkrip untuk dianalisis.")

    await require_ai_ready(db, user_id)
    job.status, job.stage, job.progress, job.error = "queued", "analyze", 60, None
    await db.commit()
    await db.refresh(job)
    dispatch_rescore(str(job.id))
    return job


async def cancel_job(db: AsyncSession, user_id: Any, job_id: UUID) -> Job:
    """Batalkan job dan putus proses FFmpeg/yt-dlp/Whisper yang sedang berjalan.

    Status ``canceled`` ditulis lebih dulu, baru proses anak dimatikan: worker
    yang melihat prosesnya mati memeriksa basis data, menemukan ``canceled``,
    lalu berhenti lewat :class:`~clipper_shared.worker_events.JobCanceled` tanpa
    menandai job (atau render) gagal.
    """
    from clipper_shared.processes import terminate_job

    job = await get_owned_job(db, user_id, job_id)
    if job.status in ACTIVE_STATUSES:
        job.status, job.error = "canceled", "Dibatalkan pengguna."
        segment_ids = select(Segment.id).where(Segment.job_id == job.id)
        await db.execute(
            update(Render)
            .where(Render.segment_id.in_(segment_ids), Render.status.in_(["queued", "running"]))
            .values(status="canceled")
        )
        await db.commit()
        await db.refresh(job)
        # Setelah status tersimpan: mematikan proses lebih dulu berlomba dengan
        # worker yang melihat proses mati dan bisa menandai job gagal.
        await asyncio.to_thread(terminate_job, str(job.id))
    return job


async def delete_job(db: AsyncSession, user_id: Any, job_id: UUID) -> None:
    """Hapus job, semua barisnya, media mentah, dan hasil render.

    Salinan bernama di ``output/clips/<job_id>`` sengaja TIDAK dihapus: itu
    folder milik pengguna, dan mungkin sudah dipakai di luar aplikasi.
    """
    job = await get_owned_job(db, user_id, job_id)
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


async def list_jobs(db: AsyncSession, user_id: Any, *, limit: int, offset: int) -> tuple[list[Job], int]:
    """Job milik user, terbaru dulu, beserta totalnya."""
    total = (await db.execute(select(func.count()).select_from(Job).where(Job.user_id == user_id))).scalar_one()
    rows = (
        await db.execute(
            select(Job)
            .where(Job.user_id == user_id)
            .order_by(Job.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return list(rows), int(total)


# --- Segmen -----------------------------------------------------------------


async def list_job_segments(db: AsyncSession, user_id: Any, job_id: UUID) -> tuple[list[Segment], str]:
    """Segmen terurut skor tertinggi, beserta catatan yang menjelaskan daftar kosong."""
    job = await get_owned_job(db, user_id, job_id)
    rows = list(
        (
            await db.execute(
                select(Segment).where(Segment.job_id == job.id).order_by(Segment.score.desc(), Segment.start_s.asc())
            )
        ).scalars().all()
    )

    note = ""
    if not rows:
        if job.status == "failed":
            note = job.error or "Job gagal sebelum analisis menghasilkan segmen."
        elif job.status in ACTIVE_STATUSES:
            note = f"Job masih pada tahap '{job.stage}'. Segmen muncul setelah transkripsi dan analisis selesai."
        else:
            note = "Analisis selesai tetapi tidak ada segmen tersimpan. Klik 'Analisis ulang' untuk mencoba lagi."
    return rows, note


async def update_segment(
    db: AsyncSession,
    user_id: Any,
    *,
    job_id: UUID,
    segment_id: UUID,
    start_s: float | None,
    end_s: float | None,
    status_value: str | None,
) -> Segment:
    """Potong awal/akhir segmen atau tandai dipilih/ditolak.

    Raises:
        UnprocessableError: rentang tidak sah atau melewati durasi video.
    """
    job = await get_owned_job(db, user_id, job_id)
    segment = await get_segment(db, job, segment_id)
    new_start = segment.start_s if start_s is None else start_s
    new_end = segment.end_s if end_s is None else end_s
    if new_end - new_start < 1:
        raise UnprocessableError("Durasi segmen minimal 1 detik.")
    media = (await db.execute(select(SourceMedia).where(SourceMedia.job_id == job.id))).scalar_one_or_none()
    if media is not None and media.duration_s and new_end > media.duration_s + 0.5:
        raise UnprocessableError(f"Akhir segmen melewati durasi video ({media.duration_s:.1f} detik).")
    segment.start_s, segment.end_s = new_start, new_end
    if status_value is not None:
        segment.status = status_value
    await db.commit()
    await db.refresh(segment)
    return segment


async def social_caption(db: AsyncSession, user_id: Any, job_id: UUID, segment_id: UUID) -> dict[str, Any]:
    """Minta penyedia AI menulis caption dan hashtag dari transkrip klip (PRD FR-4.2).

    Raises:
        UnprocessableError: transkrip kosong atau penyedia AI gagal.
    """
    from worker_light.social_caption import CaptionError, generate_social_caption

    job = await get_owned_job(db, user_id, job_id)
    segment = await get_segment(db, job, segment_id)
    try:
        result = await asyncio.to_thread(
            generate_social_caption, str(job.id), segment.start_s, segment.end_s, segment.label or ""
        )
    except CaptionError as exc:
        raise UnprocessableError(str(exc)) from exc
    return dict(result)


# --- Media & log ------------------------------------------------------------


async def get_job_media(db: AsyncSession, user_id: Any, job_id: UUID) -> SourceMedia | None:
    """Baris media sumber job, atau ``None`` bila belum ada/metadata tak lengkap."""
    job = await get_owned_job(db, user_id, job_id)
    media = (await db.execute(select(SourceMedia).where(SourceMedia.job_id == job.id))).scalar_one_or_none()
    if media is None or not media.width or not media.height:
        return None
    return media


async def job_media_file(db: AsyncSession, user_id: Any, job_id: UUID) -> Path:
    """Jalur berkas video sumber, atau 404 bila tidak ada."""
    job = await get_owned_job(db, user_id, job_id)
    media = (await db.execute(select(SourceMedia).where(SourceMedia.job_id == job.id))).scalar_one_or_none()
    return file_or_404(layout.RAW, media.r2_key if media else None, "Media sumber belum/tidak lagi tersedia.")


async def list_job_events(db: AsyncSession, user_id: Any, job_id: UUID, *, limit: int) -> tuple[list[dict[str, Any]], str]:
    """Riwayat tahapan job TERLAMA dulu; ``elapsed_s`` dihitung dari event pertama."""
    job = await get_owned_job(db, user_id, job_id)
    rows = (
        await db.execute(
            select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.created_at.asc()).limit(limit)
        )
    ).scalars().all()
    base: datetime = rows[0].created_at if rows else job.created_at
    items = [
        {
            "id": row.id,
            "stage": row.stage,
            "message": row.message,
            "elapsed_s": max(0.0, (row.created_at - base).total_seconds()),
            "created_at": row.created_at,
        }
        for row in rows
    ]
    note = ""
    if not rows:
        note = (
            f"Belum ada log. Job masih pada tahap '{job.stage}'."
            if job.status in ACTIVE_STATUSES
            else "Tidak ada log tersimpan untuk job ini."
        )
    return items, note


# --- Render -----------------------------------------------------------------


async def list_job_renders(db: AsyncSession, user_id: Any, job_id: UUID) -> list[Render]:
    """Semua render segmen job ini, terbaru dulu."""
    job = await get_owned_job(db, user_id, job_id)
    return list(
        (
            await db.execute(
                select(Render)
                .join(Segment, Segment.id == Render.segment_id)
                .where(Segment.job_id == job.id)
                .order_by(Render.created_at.desc())
            )
        ).scalars().all()
    )


async def render_job_segment(
    db: AsyncSession,
    user_id: Any,
    *,
    job_id: UUID,
    segment_id: UUID,
    kind: str,
    crop_mode: CropMode | None,
    preset: str | None,
) -> Render:
    """Jadwalkan render segmen; render aktif dengan kind sama dikembalikan apa adanya.

    Raises:
        ConflictError: media sumber sudah dihapus pembersihan otomatis.
    """
    from app.core.dispatch import dispatch_render

    job = await get_owned_job(db, user_id, job_id)
    segment = await get_segment(db, job, segment_id)
    media = (await db.execute(select(SourceMedia).where(SourceMedia.job_id == job.id))).scalar_one_or_none()
    if media is None or not layout.object_path(layout.RAW, media.r2_key).is_file():
        raise ConflictError(
            "Media sumber sudah tidak ada (dihapus 48 jam setelah render terakhir). Buat job baru."
        )

    existing = (
        await db.execute(
            select(Render)
            .where(Render.segment_id == segment.id, Render.kind == kind, Render.status.in_(["queued", "running"]))
            .order_by(Render.created_at.desc())
        )
    ).scalars().first()
    if existing is not None:
        return existing

    resolved_mode = (crop_mode or CropMode.FACE_TRACK).value
    render_row = Render(segment_id=segment.id, kind=kind, crop_mode=resolved_mode, status="queued", preset=preset)
    db.add(render_row)
    # Render baru: jangan hapus media mentah di tengah jalan; worker menetapkan
    # ulang batas 48 jam setelah render terakhir selesai.
    media.expires_at = None
    job.status, job.stage, job.error = "running", "render", None
    await db.commit()
    await db.refresh(render_row)

    dispatch_render(str(job.id), str(segment.id), kind, preset, resolved_mode)
    return render_row


async def render_file(db: AsyncSession, user_id: Any, job_id: UUID, render_id: UUID) -> tuple[Render, Path]:
    """Baris render dan jalur berkasnya, atau 404 bila berkas belum ada."""
    job = await get_owned_job(db, user_id, job_id)
    render_row = await get_render(db, job, render_id)
    path = file_or_404(layout.RENDERS, render_row.r2_key, "Berkas render belum tersedia.")
    return render_row, path
