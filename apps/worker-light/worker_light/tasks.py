"""Task worker-light: ``ingest_media``, ``transcribe_media``, ``score_segments``.

Fungsi biasa yang dijalankan :mod:`clipper_shared.dispatcher` di thread pool
proses API. Batas paralel (mis. satu transkripsi sekaligus) ditegakkan oleh
ukuran pool, bukan oleh kode di sini.

Setiap task menulis status lewat :func:`clipper_shared.worker_events.emit`.
``emit`` melempar :class:`JobCanceled` bila pengguna membatalkan/menghapus
job; task menangkapnya dan berhenti tanpa menandai job gagal.
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any

from clipper_shared.ai_provider import effective_allow_private, max_video_minutes
from clipper_shared.dispatcher import submit
from clipper_shared.scoring import MIN_SEGMENTS, max_clips_for_duration
from clipper_shared.storage import repo_path
from clipper_shared.worker_events import JobCanceled, emit, emit_failed
from clipper_shared.workspace import make_workspace

logger = logging.getLogger(__name__)

#: Bawaan bila ``jobs.clip_count`` tidak terbaca.
DEFAULT_CLIP_COUNT = 5


def _load_clip_count(job_id: str) -> int:
    """Baca jumlah klip yang diminta pengguna untuk sebuah job."""
    from clipper_shared.db import get_db_connection

    try:
        with get_db_connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT clip_count FROM jobs WHERE id = %s", (job_id,))
            row = cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 — kegagalan membaca tidak boleh menghentikan tahap
        logger.warning("Gagal membaca clip_count job %s: %s", job_id, exc)
        return DEFAULT_CLIP_COUNT
    return int(row[0]) if row and row[0] is not None else DEFAULT_CLIP_COUNT


def ingest_media(job_id: str, source_type: str, source_url: str | None = None) -> dict[str, Any] | None:
    """Tahap ingest: ambil media, baca metadatanya, lalu lanjut ke transkripsi.

    YouTube: metadata dan durasi divalidasi sebelum mengunduh; bila video punya
    subtitle, subtitle dipakai dan Whisper dilewati. Unggahan: berkas sudah ada
    di penyimpanan (``/uploads/.../complete``) dan hanya diperiksa.
    """
    from worker_light import storage
    from worker_light.media_fetcher import (
        IngestError,
        download_subtitle,
        download_youtube,
        fetch_youtube_metadata,
        select_subtitle_track,
        validate_duration,
    )

    work_dir = make_workspace(f"ingest-{job_id[:8]}-")
    try:
        emit(job_id, "running", "ingest", 5, "Ingest dimulai")
        max_minutes = max_video_minutes(storage.load_provider_config(job_id).get("context_tokens"))
        subtitle_words: list[dict[str, object]] = []
        language: str | None = None

        if source_type == "youtube":
            if not source_url:
                raise IngestError("URL YouTube tidak diberikan.")
            emit(job_id, "running", "ingest", 15, "Membaca metadata video")
            cookies_path = storage.fetch_youtube_cookies(job_id, work_dir)
            metadata = fetch_youtube_metadata(source_url, cookies_path)
            validate_duration(metadata, max_minutes)

            # JALUR CEPAT: subtitle yang sudah ada memangkas tahap terpanjang
            # (Whisper di CPU) dari puluhan menit menjadi hitungan detik.
            track = select_subtitle_track(metadata)
            if track is not None:
                emit(job_id, "running", "ingest", 25, f"Memakai subtitle {track.lang} — Whisper dilewati")
                words, quality = download_subtitle(track, work_dir, cookies_path)
                if words:
                    language = track.base_lang
                    subtitle_words = [
                        {"text": w.text, "start_s": w.start_s, "end_s": w.end_s}  # type: ignore[attr-defined]
                        for w in words
                    ]
                    emit(job_id, "running", "ingest", 40, f"Subtitle diterima ({quality})")

            emit(job_id, "running", "ingest", 45, "Mengunduh video")
            downloaded = download_youtube(source_url, work_dir, cookies_path)
            probe = storage.probe_media(downloaded)
            r2_key = storage.store_downloaded_media(job_id, downloaded)
        elif source_type == "upload":
            emit(job_id, "running", "ingest", 20, "Membaca berkas unggahan")
            media_path = storage.source_media_path(job_id)
            probe = storage.probe_media(media_path)
            # Unggahan tidak punya metadata YouTube; batas durasi dicek dari
            # probe, SEBELUM transkripsi yang bisa makan waktu berjam-jam.
            if probe["duration_s"] > max_minutes * 60:
                raise IngestError(
                    f"Video berdurasi {int(probe['duration_s'] // 60)} menit, melebihi batas "
                    f"{max_minutes} menit (kapasitas konteks model AI atau MAX_VIDEO_DURATION_MIN)."
                )
            r2_key = storage.source_media_r2_key(job_id)
        else:
            raise ValueError(f"source_type harus 'upload' atau 'youtube', dapat {source_type!r}")

        emit(job_id, "running", "ingest", 60, "Metadata media dibaca")
        storage.record_source_media(
            job_id=job_id,
            r2_key=r2_key,
            size_bytes=probe["size_bytes"],
            duration_s=probe["duration_s"],
            width=probe["width"],
            height=probe["height"],
            codec=probe["codec"],
            language=language,
            transcript_source="youtube_subtitle" if subtitle_words else None,
        )

        if subtitle_words:
            storage.save_transcript(
                job_id=job_id,
                language=language,
                words=subtitle_words,
                full_text=" ".join(str(w["text"]) for w in subtitle_words),
                model_used="youtube_subtitle",
            )
            storage.save_segments(job_id=job_id, words=subtitle_words)
            emit(job_id, "running", "analyze", 70, "Ingest selesai")
            submit("worker_light.tasks.score_segments", job_id)
        else:
            emit(job_id, "running", "transcribe", 70, "Ingest selesai, menunggu giliran transkripsi")
            submit("worker_light.tasks.transcribe_media", job_id)
        return {"job_id": job_id, "r2_key": r2_key, "skipped_whisper": bool(subtitle_words)}
    except JobCanceled:
        return None
    except Exception as exc:
        logger.exception("Ingest gagal untuk job %s", job_id)
        emit_failed(job_id, "ingest", f"Ingest gagal: {exc}")
        return None
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _transcriber() -> Any:
    from clipper_shared.stt import get_transcriber

    return get_transcriber(
        backend=os.getenv("STT_BACKEND", "local"),
        download_root=str(repo_path("WHISPER_MODEL_CACHE_DIR", ".models")),
        model_size=os.getenv("WHISPER_MODEL", "small"),
        compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "int8"),
        api_key=os.getenv("WHISPER_API_KEY") or None,
        base_url=os.getenv("WHISPER_API_BASE_URL") or None,
    )


def transcribe_media(job_id: str, language: str | None = None) -> dict[str, Any] | None:
    """Tahap transkripsi (pool ``stt``: satu per satu secara bawaan)."""
    from worker_light import storage

    work_dir = make_workspace(f"stt-{job_id[:8]}-")
    try:
        emit(job_id, "running", "transcribe", 25, "Transkripsi mulai")
        media_path = storage.source_media_path(job_id)
        transcriber = _transcriber()

        emit(job_id, "running", "transcribe", 32, "Mengekstrak audio")
        if os.getenv("STT_BACKEND", "local").strip().lower() == "remote":
            chunks = storage.extract_audio_chunks(media_path, work_dir)
        else:
            chunks = [(storage.extract_audio(media_path, work_dir), 0.0)]

        words: list[dict[str, object]] = []
        texts: list[str] = []
        detected = language
        model_used = ""
        for index, (audio_path, offset) in enumerate(chunks, start=1):
            emit(job_id, "running", "transcribe", 40 + int(20 * (index - 1) / len(chunks)),
                 f"Menjalankan pengenalan suara ({index}/{len(chunks)})")
            result = transcriber.transcribe(str(audio_path), language=detected)
            detected = detected or result.language or None
            model_used = result.model_used
            texts.append(result.full_text)
            words.extend(
                {
                    "text": w.word,
                    "start_s": w.start_s + offset,
                    "end_s": w.end_s + offset,
                    "speaker": getattr(w, "speaker", None),
                }
                for w in result.words
            )

        storage.save_transcript(
            job_id=job_id,
            language=detected,
            words=words,
            full_text=" ".join(t for t in texts if t),
            model_used=model_used,
        )
        storage.save_segments(job_id=job_id, words=words)
        emit(job_id, "running", "analyze", 65, "Transkripsi selesai")
        submit("worker_light.tasks.score_segments", job_id)
        return {"job_id": job_id, "language": detected, "word_count": len(words)}
    except JobCanceled:
        return None
    except Exception as exc:
        logger.exception("Transkripsi gagal untuk job %s", job_id)
        emit_failed(job_id, "transcribe", f"Transkripsi gagal: {exc}")
        return None
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def score_segments(job_id: str, target_count: int | None = None) -> dict[str, Any] | None:
    """Tahap analisis: minta kandidat klip ke LLM, simpan, lalu render.

    Bila LLM gagal atau tidak menghasilkan segmen yang lolos validasi, kandidat
    cadangan berbasis kepadatan bicara dipakai: satu gangguan penyedia AI tidak
    boleh membuang transkripsi yang sudah memakan waktu lama. Pengguna dapat
    menjalankan ulang analisis AI lewat ``POST /jobs/{id}/rescore``.
    """
    from worker_light import storage
    from worker_light.scoring_client import heuristic_segments, score_job

    try:
        transcript = storage.load_transcript(job_id)
        if transcript is None or not transcript["words"]:
            emit(job_id, "failed", "analyze", 0, "Transkrip kosong; tidak ada yang bisa dinilai.")
            return None

        requested = _load_clip_count(job_id) if target_count is None else target_count
        duration_s = float(transcript["words"][-1].get("end_s") or 0.0)
        ceiling = max_clips_for_duration(duration_s)
        clamped = max(MIN_SEGMENTS, min(ceiling, requested))
        note = (
            f" (diminta {requested}; video {duration_s / 60:.0f} menit maks {ceiling} klip)"
            if clamped < requested
            else ""
        )
        emit(job_id, "running", "analyze", 70, f"Scoring {clamped} kandidat segmen{note}")

        provider_config = storage.load_provider_config(job_id)
        outcome = score_job(
            words=transcript["words"],
            target_count=clamped,
            provider=provider_config,
            user_direction=str(provider_config.get("default_direction") or ""),
            allow_private=effective_allow_private(provider_config),
        )

        segments = outcome.segments
        summary = f"{len(segments)} segmen ditemukan"
        if not segments:
            segments = heuristic_segments(transcript["words"], clamped)
            reason = outcome.error or "AI tidak menghasilkan segmen yang lolos validasi (durasi 25–65 detik)."
            if not segments:
                emit(job_id, "failed", "analyze", 0, f"{reason} Transkrip terlalu pendek untuk dipotong tanpa AI.")
                return None
            summary = f"{reason} Memakai {len(segments)} segmen otomatis tanpa AI"

        segment_ids = storage.replace_segments(job_id=job_id, candidates=segments)

        if os.getenv("AUTO_RENDER_FINAL", "true").lower() == "true":
            storage.queue_renders(segment_ids, "final")
            emit(job_id, "running", "render", 75, f"{summary}. Merender semua klip final...")
            for segment_id in segment_ids:
                submit("worker_render.tasks.render_clip", job_id, segment_id, "final")
        else:
            emit(job_id, "done", "done", 100, f"{summary}; siap ditinjau.")
        return {"job_id": job_id, "segments": len(segment_ids), "provider": outcome.provider_label}
    except JobCanceled:
        return None
    except Exception as exc:
        logger.exception("Scoring gagal untuk job %s", job_id)
        emit_failed(job_id, "analyze", f"Analisis skoring gagal: {exc}")
        return None
