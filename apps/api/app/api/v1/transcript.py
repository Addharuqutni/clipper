"""Router transcript: baca dan edit kata hasil STT per kata.

**Mengapa endpoint ini ada.** Transkripsi otomatis selalu punya salah dengar —
nama orang, istilah teknis, angka. Sebelum ini, pengguna tidak punya cara
memperbaikinya: kata yang salah ikut ter-bakar ke dalam video, dan satu-satunya
jalan keluar adalah mengunduh ulang dengan pengaturan berbeda (yang biasanya
menghasilkan kesalahan yang sama).

Endpoint di sini membuka transkrip sebagai **daftar kata yang dapat diedit**,
dengan waktu masing-masing. UI memakai ini untuk editor teks dan timeline.

Dua hal yang sengaja dijaga:

1. **Waktu tidak boleh diubah pengguna.** Editor ini untuk memperbaiki teks,
   bukan menyusun ulang timing. ``start_s``/``end_s`` hanya dibaca. Membiarkan
   pengguna menggeser waktu akan merusak sinkronisasi karaoke dan menghasilkan
   subtitle yang muncul sebelum kata diucapkan.
2. **Transkrip di-*mutasi* di tempat, bukan disalin.** Baris ``transcripts``
   adalah satu-satunya sumber kata bagi render; menyalinnya akan membuat dua
   kebenaran yang bisa menyimpang.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.v1.auth import CurrentUserOrDev, DbSession, get_owned_job
from app.models.transcript import Transcript

router = APIRouter()

#: Batas panjang satu kata. Bukan batas teknis libass, melainkan penjaga agar
#: pengguna tidak menempelkan paragraf ke satu kata — teks sepanjang itu akan
#: meluber keluar frame dan membingungkan saat dirender.
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
    #: Catatan yang menjelaskan keadaan kosong, sama polanya dengan daftar segmen.
    note: str = ""


class WordUpdateRequest(BaseModel):
    """Perubahan teks satu kata."""

    #: Teks baru. Boleh kosong untuk MENGHAPUS kata (mis. suara batuk yang
    #: terdeteksi sebagai kata). Kata kosong dibuang dari daftar, bukan
    #: disimpan sebagai string kosong — string kosong akan menjadi cue ASS
    #: tanpa teks yang tetap memakan durasi tampil.
    text: str = Field(default="", max_length=MAX_WORD_CHARS)
    #: Teks kata di indeks itu menurut klien. Bila berbeda dari server, klien
    #: memegang transkrip basi (tab lain menghapus kata, indeks bergeser) dan
    #: edit ditolak alih-alih mengubah kata yang salah.
    expected_text: str | None = Field(default=None, max_length=MAX_WORD_CHARS)


class WordUpdateResponse(BaseModel):
    """Hasil satu perubahan kata."""

    job_id: UUID
    index: int
    #: True bila kata dihapus (teks baru kosong).
    removed: bool
    word_count: int


def _normalize_words(raw: Any) -> list[dict[str, Any]]:
    """Ubah JSONB mentah menjadi daftar kata yang konsisten.

    Kunci waktu di basis data bisa berbentuk ``start_s``/``end_s`` (keluaran
    Whisper) atau ``start``/``end`` (keluaran subtitle YouTube). Keduanya sah
    dan sudah ada di data nyata, jadi keduanya diterima di sini — kalau tidak,
    transkrip dari subtitle YouTube akan tampil kosong di editor walau isinya
    ada.
    """
    words: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return words

    for entry in raw:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text") or entry.get("word") or "").strip()
        if not text:
            continue
        try:
            start_s = float(entry.get("start_s") or entry.get("start") or 0.0)
            end_s = float(entry.get("end_s") or entry.get("end") or 0.0)
        except (TypeError, ValueError):
            continue
        # Kunci lain (mis. ``speaker``) dipertahankan: edit pertama tidak boleh
        # membuang data yang tidak ditampilkan editor.
        words.append({**entry, "text": text, "start_s": start_s, "end_s": end_s})
    return words



async def _get_transcript(db: DbSession, job_id: UUID) -> Transcript | None:
    """Ambil transkrip pertama job (satu baris per bahasa di MVP)."""
    return (
        await db.execute(
            select(Transcript).where(Transcript.job_id == job_id).order_by(Transcript.created_at.desc()).limit(1)
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
    """Kembalikan transkrip job sebagai daftar kata bertimestamp.

    Bila belum ada transkrip, ``words`` kosong dan ``note`` menjelaskan
    alasannya — pengguna perlu tahu apakah ini "belum selesai" atau "gagal".
    """
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

    words = _normalize_words(transcript.words)
    return TranscriptResponse(
        job_id=job.id,
        language=transcript.language,
        model_used=transcript.model_used,
        full_text=transcript.full_text,
        words=[
            WordResponse(index=i, text=w["text"], start_s=w["start_s"], end_s=w["end_s"])
            for i, w in enumerate(words)
        ],
        note="" if words else "Transkrip tersimpan tetapi tidak berisi kata.",
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
    """Perbaiki ejaan satu kata, atau hapus kata bila teks baru kosong.

    Raises:
        HTTPException: 404 bila job/transkrip tidak ada; 409 bila job sedang
            dirender (mengubah teks di tengah render membuat hasil tidak
            konsisten dengan yang dilihat pengguna); 422 bila indeks di luar
            rentang.
    """
    job = await get_owned_job(db, current_user.id, job_id)

    # Menolak edit saat render berjalan: worker sudah membaca kata-kata ini dan
    # sedang membakarnya ke video. Mengizinkan edit di sini akan membuat
    # perubahan pengguna hilang tanpa penjelasan saat render selesai.
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

    words = _normalize_words(transcript.words)
    if index >= len(words):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Indeks kata {index} di luar rentang (0–{max(0, len(words) - 1)}).",
        )

    if payload.expected_text is not None and words[index]["text"] != payload.expected_text.strip():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Transkrip sudah berubah di tempat lain. Muat ulang lalu coba lagi.",
        )

    new_text = payload.text.strip()
    removed = False
    if new_text:
        words[index]["text"] = new_text
    else:
        words.pop(index)
        removed = True

    # SQLAlchemy tidak melacak mutasi di dalam JSONB. Menetapkan objek BARU
    # (bukan memutasi lalu menyimpan) yang memastikan perubahan terdeteksi —
    # ini penyebab umum "perubahan tidak tersimpan" pada kolom JSONB.
    transcript.words = list(words)
    # ``full_text`` ikut diperbarui supaya prompt LLM dan pencarian teks tetap
    # konsisten dengan kata-kata yang sudah diperbaiki.
    transcript.full_text = " ".join(str(w["text"]) for w in words)
    await db.commit()

    return WordUpdateResponse(
        job_id=job.id,
        index=index,
        removed=removed,
        word_count=len(words),
    )
