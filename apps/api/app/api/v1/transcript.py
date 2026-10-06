"""Router transcript: baca dan edit kata hasil STT per kata.

Route ini bertindak sebagai adaptor HTTP murni: validasi payload request,
pemeriksaan status job, pendelegasian mutasi teks ke :mod:`clipper_shared.transcript`,
dan pemetaan domain error ke HTTP status code.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from clipper_shared.transcript import (
    TranscriptConflictError,
    WordIndexOutOfBoundsError,
    build_full_text,
    normalize_words,
    update_word_in_list,
)
from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.v1.auth import CurrentUserOrDev, DbSession
from app.models.transcript import Transcript
from app.services.jobs import get_owned_job

router = APIRouter()

MAX_WORD_CHARS = 120


class WordResponse(BaseModel):
    """Satu kata bertimestamp."""

    index: int
    text: str
    start_s: float
    end_s: float


class TranscriptResponse(BaseModel):
    """Transkrip lengkap sebuah job."""

    job_id: UUID
    language: str | None
    model_used: str | None
    full_text: str | None
    words: list[WordResponse]
    note: str = ""


class WordUpdateRequest(BaseModel):
    """Perubahan teks satu kata."""

    text: str = Field(default="", max_length=MAX_WORD_CHARS)
    expected_text: str | None = Field(default=None, max_length=MAX_WORD_CHARS)


class WordUpdateResponse(BaseModel):
    """Hasil satu perubahan kata."""

    job_id: UUID
    index: int
    removed: bool
    word_count: int


async def _get_transcript(db: DbSession, job_id: UUID) -> Transcript | None:
    """Ambil transkrip pertama job."""
    return (
        await db.execute(
            select(Transcript)
            .where(Transcript.job_id == job_id)
            .order_by(Transcript.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


@router.get(
    "/{job_id}/transcript",
    response_model=TranscriptResponse,
    summary="Transkrip per kata untuk sebuah job",
)
async def get_transcript(
    job_id: UUID,
    current_user: CurrentUserOrDev,
    db: DbSession,
) -> TranscriptResponse:
    """Kembalikan transkrip job sebagai daftar kata bertimestamp kanonikal."""
    job = await get_owned_job(db, current_user.id, job_id)
    transcript = await _get_transcript(db, job.id)

    if transcript is None:
        note = (
            f"Transkrip belum ada. Job masih pada tahap '{job.stage}'. "
            "Teks muncul setelah transkripsi selesai."
            if job.status in {"queued", "running"}
            else "Tidak ada transkrip tersimpan untuk job ini."
        )
        return TranscriptResponse(
            job_id=job.id,
            language=None,
            model_used=None,
            full_text=None,
            words=[],
            note=note,
        )

    canonical_words = normalize_words(transcript.words)
    return TranscriptResponse(
        job_id=job.id,
        language=transcript.language,
        model_used=transcript.model_used,
        full_text=transcript.full_text or build_full_text(canonical_words),
        words=[
            WordResponse(index=i, text=w.text, start_s=w.start_s, end_s=w.end_s)
            for i, w in enumerate(canonical_words)
        ],
        note="" if canonical_words else "Transkrip tersimpan tetapi tidak berisi kata.",
    )


@router.patch(
    "/{job_id}/transcript/words/{index}",
    response_model=WordUpdateResponse,
    summary="Ubah teks satu kata pada transkrip",
)
async def update_word(
    job_id: UUID,
    index: Annotated[int, Path(ge=0)],
    payload: WordUpdateRequest,
    current_user: CurrentUserOrDev,
    db: DbSession,
) -> WordUpdateResponse:
    """Perbaiki ejaan satu kata, atau hapus kata bila teks baru kosong."""
    job = await get_owned_job(db, current_user.id, job_id)

    if job.stage == "render" and job.status == "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Job sedang dirender. Mengubah teks sekarang tidak akan ikut "
                "ter-render dan perubahan bisa hilang. Tunggu sampai selesai."
            ),
        )

    transcript = await _get_transcript(db, job.id)
    if transcript is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transkrip tidak ditemukan untuk job ini.",
        )

    words = normalize_words(transcript.words)

    try:
        updated_words, removed = update_word_in_list(
            words,
            index=index,
            new_text=payload.text,
            expected_text=payload.expected_text,
        )
    except WordIndexOutOfBoundsError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except TranscriptConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    transcript.words = [w.to_dict() for w in updated_words]
    transcript.full_text = build_full_text(updated_words)
    await db.commit()

    return WordUpdateResponse(
        job_id=job.id,
        index=index,
        removed=removed,
        word_count=len(updated_words),
    )
