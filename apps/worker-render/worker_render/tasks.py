"""Task worker-render: ``render_clip`` dan ``purge_expired_raw_media``.

``render_clip`` adalah task paling mahal: FFmpeg + MediaPipe pada CPU. Ia
dijalankan di pool ``render`` (ukuran ``RENDER_SLOTS``, bawaan 1) dan memberi
FFmpeg ``-threads N`` eksplisit supaya API tetap responsif (TECH_SPEC §4.3).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import uuid
from dataclasses import fields
from pathlib import Path
from typing import Any

from clipper_shared import storage as layout
from clipper_shared.db import get_db_connection, utc_now
from clipper_shared.reframe import CropMode
from clipper_shared.subtitles import SubtitleStyle, SubtitleWord
from clipper_shared.worker_events import JobCanceled, emit
from clipper_shared.workspace import make_workspace

logger = logging.getLogger(__name__)


def _render_settings() -> dict[str, Any]:
    """Pengaturan encoding dari env (TECH_SPEC §4.3), dibaca saat pemanggilan."""
    return {
        "preview_crf": int(os.getenv("RENDER_PREVIEW_CRF", "30")),
        "final_crf": int(os.getenv("RENDER_FINAL_CRF", "18")),
        "preview_preset": os.getenv("RENDER_PREVIEW_PRESET", "veryfast"),
        "final_preset": os.getenv("RENDER_FINAL_PRESET", "slow"),
    }


def _ffmpeg_threads() -> int:
    """``-threads N`` untuk FFmpeg. Jangan biarkan FFmpeg memakai seluruh core."""
    return int(os.getenv("FFMPEG_THREADS", "2"))


def _resolve_crop_mode(raw: str | None) -> CropMode:
    """Nilai mode dari DB menjadi enum; nilai tak dikenal jatuh ke face_track."""
    try:
        return CropMode(raw or CropMode.FACE_TRACK.value)
    except ValueError:
        logger.warning("Mode crop %r tidak dikenal; memakai face_track.", raw)
        return CropMode.FACE_TRACK


def _limit_opencv_threads() -> None:
    """Batasi thread OpenCV agar tidak berebut core dengan FFmpeg (TECH_SPEC §4.3)."""
    try:
        import cv2

        cv2.setNumThreads(int(os.getenv("CV_NUM_THREADS", "1")))
    except ImportError:
        logger.debug("OpenCV tidak tersedia; CV_NUM_THREADS dilewati")


# --- Akses data -------------------------------------------------------------


def _source_path(job_id: str) -> Path:
    """Jalur media sumber job. Dibaca di tempat: tidak ada salinan per klip."""
    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT r2_key FROM source_media WHERE job_id = %s", (job_id,))
        row = cursor.fetchone()
    if row is None or not row[0]:
        raise RuntimeError(f"source_media untuk job {job_id} tidak ditemukan")
    path = layout.object_path(layout.RAW, row[0])
    if not path.is_file():
        raise FileNotFoundError(
            f"Media sumber tidak ada di disk ({path}); mungkin sudah dihapus "
            "pembersihan otomatis 48 jam setelah render terakhir."
        )
    return path


def _fetch_segment_range(segment_id: str) -> dict[str, Any]:
    """Rentang waktu dan label segmen."""
    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT start_s, end_s, label FROM segments WHERE id = %s", (segment_id,))
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError(f"segmen {segment_id} tidak ditemukan")
    return {"start_s": float(row[0]), "end_s": float(row[1]), "label": str(row[2] or "")}


def _fetch_words(job_id: str, start_s: float, end_s: float) -> list[SubtitleWord]:
    """Kata transkrip di dalam rentang segmen, digeser ke waktu relatif klip."""
    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT words FROM transcripts WHERE job_id = %s ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        )
        row = cursor.fetchone()
    if row is None or not row[0]:
        return []

    raw_words = row[0]
    if isinstance(raw_words, str):
        try:
            raw_words = json.loads(raw_words)
        except json.JSONDecodeError:
            return []

    words: list[SubtitleWord] = []
    for entry in raw_words:
        if not isinstance(entry, dict):
            continue
        word_start = float(entry.get("start_s") or entry.get("start") or 0.0)
        word_end = float(entry.get("end_s") or entry.get("end") or 0.0)
        text = str(entry.get("text") or entry.get("word") or "").strip()
        if not text or word_end <= word_start or word_end <= start_s or word_start >= end_s:
            continue
        words.append(
            SubtitleWord(
                text=text,
                start_s=max(0.0, word_start - start_s),
                end_s=max(0.05, word_end - start_s),
            )
        )
    return words


def _load_subtitle_style(job_id: str) -> dict[str, Any] | None:
    """Gaya subtitle: override per job, lalu preset bawaan pengguna, lalu ``None``."""

    def as_dict(value: Any) -> dict[str, Any] | None:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return None
        return dict(value) if value else None

    try:
        with get_db_connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT subtitle_style FROM jobs WHERE id = %s", (job_id,))
            row = cursor.fetchone()
            if row is not None and (style := as_dict(row[0])):
                return style
            cursor.execute(
                """
                SELECT sp.style
                  FROM users u
                  JOIN subtitle_presets sp ON sp.id = u.default_subtitle_preset_id
                 WHERE u.id = (SELECT user_id FROM jobs WHERE id = %s)
                """,
                (job_id,),
            )
            row = cursor.fetchone()
            if row is not None:
                return as_dict(row[0])
    except Exception as exc:  # noqa: BLE001 — gaya gagal dibaca bukan alasan menggagalkan render
        logger.warning("Gagal membaca gaya subtitle job %s: %s", job_id, exc)
    return None


def _style_from_payload(payload: dict[str, Any] | None) -> SubtitleStyle:
    """Dict gaya dari DB menjadi :class:`SubtitleStyle`; kunci asing dibuang."""
    if not payload:
        return SubtitleStyle()
    known = {f.name for f in fields(SubtitleStyle)}
    unknown = set(payload) - known
    if unknown:
        logger.warning("Mengabaikan %d kunci gaya yang tidak dikenal: %s", len(unknown), sorted(unknown))
    try:
        return SubtitleStyle(**{k: v for k, v in payload.items() if k in known})
    except (TypeError, ValueError) as exc:
        logger.warning("Gaya subtitle tidak sah (%s); memakai gaya bawaan.", exc)
        return SubtitleStyle()


def _fonts_dir(job_id: str) -> Path | None:
    """Folder font kustom milik pemilik job (lihat ``app.core.storage.store_font_bytes``)."""
    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT user_id FROM jobs WHERE id = %s", (job_id,))
        row = cursor.fetchone()
    return layout.storage_root() / layout.FONTS / "fonts" / str(row[0]) if row else None


def _load_segment_overlays(segment_id: str) -> list[Any]:
    """Overlay segmen (waktu relatif awal segmen), dibaca langsung dari disk."""
    from worker_render.overlay_compositor import OverlaySpec

    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT so.start_s, so.end_s, so.position, so.scale, so.opacity,
                   so.transition, so.transition_ms, oa.kind, oa.r2_key, oa.name
              FROM segment_overlays so
              JOIN overlay_assets oa ON oa.id = so.asset_id
             WHERE so.segment_id = %s
             ORDER BY so.start_s
            """,
            (segment_id,),
        )
        rows = cursor.fetchall()

    specs: list[Any] = []
    for start_s, end_s, position, scale, opacity, transition, transition_ms, kind, r2_key, name in rows:
        path = layout.object_path(layout.OVERLAYS, str(r2_key))
        if not path.is_file():
            # Satu overlay hilang jauh lebih baik daripada klip yang gagal total.
            logger.warning("Melewati overlay '%s': berkas tidak ada (%s)", name, path)
            continue
        specs.append(
            OverlaySpec(
                path=path,
                start_s=float(start_s),
                end_s=float(end_s),
                position=str(position),
                scale=float(scale),
                opacity=int(opacity),
                kind=str(kind),
                transition=str(transition),
                transition_ms=int(transition_ms),
            )
        )
    return specs


def _describe_clip(*, job_id: str, segment_id: str, label: str, kind: str, order: int = 1) -> str:
    """Nama berkas deskriptif untuk salinan di ``output/clips``::

        <job>_<segmen>_<urutan>_<slug-label>_<jenis>.mp4
    """
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40].rstrip("-")
    return f"{str(job_id)[:8]}_{str(segment_id)[:8]}_{order:02d}_{slug or 'segmen'}_{kind}.mp4"


def _store_render(segment_id: str, kind: str, path: Path, *, job_id: str, label: str) -> str:
    """Pindahkan hasil ke penyimpanan kanonik; buat salinan bernama di ``output/clips``.

    ``renders/<segment_id>/<kind>.mp4`` adalah yang dirujuk ``renders.r2_key``.
    Salinan untuk manusia dibuat sebagai hard link bila bisa (tanpa ruang disk
    tambahan), dan disalin bila tidak (beda drive).
    """
    key = f"renders/{segment_id}/{kind}.mp4"
    target = layout.object_path(layout.RENDERS, key)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), target)

    clips_dir = layout.repo_path("CLIPS_OUTPUT_DIR", "output/clips")
    clips_dir.mkdir(parents=True, exist_ok=True)
    human = clips_dir / _describe_clip(job_id=job_id, segment_id=segment_id, label=label, kind=kind)
    human.unlink(missing_ok=True)
    try:
        os.link(target, human)
    except OSError:
        shutil.copy2(target, human)
    return key


def _set_render(segment_id: str, kind: str, **values: Any) -> None:
    """Perbarui render aktif (queued/running) terbaru untuk segmen+kind, atau buat baru."""
    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id FROM renders
             WHERE segment_id = %s AND kind = %s AND status IN ('queued', 'running')
             ORDER BY created_at DESC LIMIT 1
            """,
            (segment_id, kind),
        )
        row = cursor.fetchone()
        if row is None:
            render_id = str(uuid.uuid4())
            cursor.execute(
                "INSERT INTO renders (id, segment_id, kind, crop_mode, status, created_at) "
                "VALUES (%s, %s, %s, %s, 'queued', %s)",
                (render_id, segment_id, kind, values.get("crop_mode", "face_track"), utc_now()),
            )
        else:
            render_id = row[0]
        assignments = ", ".join(f"{column} = %s" for column in values)
        cursor.execute(f"UPDATE renders SET {assignments} WHERE id = %s", (*values.values(), render_id))  # noqa: S608


def _finish_job_status(job_id: str) -> None:
    """Turunkan status job dari render TERBARU tiap segmen.

    Sebelumnya status mengikuti render yang kebetulan selesai terakhir: satu klip
    gagal di akhir membuat 9 klip bagus tampil sebagai job gagal, dan kegagalan
    di awal tertutup oleh sukses berikutnya.
    """
    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT r.segment_id, r.status FROM renders r
              JOIN segments s ON s.id = r.segment_id
             WHERE s.job_id = %s
             ORDER BY r.created_at
            """,
            (job_id,),
        )
        latest = dict(cursor.fetchall())  # baris terurut waktu: yang terakhir menang

    statuses = list(latest.values())
    pending = sum(s in {"queued", "running"} for s in statuses)
    done = statuses.count("done")
    failed = statuses.count("failed")
    total = len(statuses)

    if pending:
        emit(job_id, "running", "render", 75 + int(25 * (done + failed) / max(total, 1)) - 1,
             f"{done + failed}/{total} klip selesai dirender")
        return

    # Render terakhir selesai: hitung mundur 48 jam retensi media mentah dari sini.
    from clipper_shared.maintenance import raw_media_expiry

    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE source_media SET expires_at = %s WHERE job_id = %s", (raw_media_expiry(), job_id))

    if done == 0:
        emit(job_id, "failed", "render", 0, f"Semua {failed} render gagal. Lihat log untuk detail.")
    elif failed:
        emit(job_id, "done", "done", 100, f"{done} klip selesai, {failed} gagal (bisa dirender ulang).")
    else:
        emit(job_id, "done", "done", 100, f"{done} klip selesai.")


# --- Task -------------------------------------------------------------------


def render_clip(
    job_id: str,
    segment_id: str,
    kind: str = "preview",
    preset: str | None = None,
    crop_mode: str | None = None,
) -> dict[str, Any] | None:
    """Render satu segmen menjadi klip 9:16 dalam satu pass encode.

    Args:
        job_id: UUID job.
        segment_id: UUID segmen.
        kind: ``preview`` (540×960, cepat) atau ``final`` (1080×1920, kualitas).
        preset: label preset yang dicatat di baris render.
        crop_mode: ``face_track`` / ``black_bars`` / ``blurred_fill``.
    """
    if kind not in {"preview", "final"}:
        raise ValueError(f"kind harus 'preview' atau 'final', dapat {kind!r}")

    from worker_render.reframer import (
        ReframeOptions,
        default_model_path,
        render_segment,
        write_subtitles,
    )

    mode = _resolve_crop_mode(crop_mode)
    work_dir = make_workspace(f"render-{str(segment_id)[:8]}-")
    try:
        _limit_opencv_threads()
        _set_render(segment_id, kind, status="running", crop_mode=mode.value)
        emit(job_id, "running", "render", 80, f"Render {kind} dimulai (threads={_ffmpeg_threads()})")

        settings = _render_settings()
        source_path = _source_path(job_id)
        segment = _fetch_segment_range(segment_id)
        is_preview = kind == "preview"
        options = ReframeOptions(
            mode=mode,
            start_s=segment["start_s"],
            end_s=segment["end_s"],
            crf=settings["preview_crf"] if is_preview else settings["final_crf"],
            preset=settings["preview_preset"] if is_preview else settings["final_preset"],
            output_width=540 if is_preview else 1080,
            output_height=960 if is_preview else 1920,
            ffmpeg_threads=_ffmpeg_threads(),
            face_model_path=default_model_path(),
        )

        words = _fetch_words(job_id, segment["start_s"], segment["end_s"])
        style_payload = _load_subtitle_style(job_id)
        style = _style_from_payload(style_payload)
        subtitles = write_subtitles(words, work_dir, options=options, style=style) if words else None

        emit(job_id, "running", "render", 82, f"Mode {mode.value}: merender segmen")
        rendered = work_dir / "rendered.mp4"
        result = render_segment(
            source_path, rendered, options,
            work_dir=work_dir, subtitles=subtitles, fonts_dir=_fonts_dir(job_id), job_id=job_id,
        )
        if result.get("tracking_health"):
            logger.info("Kesehatan pelacakan: %s", result["tracking_health"])

        overlay_specs = _load_segment_overlays(str(segment_id))
        if overlay_specs:
            from worker_render.overlay_compositor import composite_overlays

            emit(job_id, "running", "render", 95, f"Mengomposisikan {len(overlay_specs)} overlay")
            composited = work_dir / "composited.mp4"
            try:
                composite_overlays(
                    rendered,
                    composited,
                    overlay_specs,
                    width=options.output_width,
                    height=options.output_height,
                    crf=options.crf,
                    preset=options.preset,
                    ffmpeg_threads=options.ffmpeg_threads,
                    job_id=job_id,
                )
                rendered = composited
            except RuntimeError as exc:
                # Overlay adalah penyempurnaan: klip tanpa B-roll lebih berguna
                # daripada klip yang gagal seluruhnya.
                logger.warning("Gagal mengomposisikan overlay: %s", exc)

        size_bytes = rendered.stat().st_size
        r2_key = _store_render(segment_id, kind, rendered, job_id=job_id, label=segment["label"])
        _set_render(
            segment_id,
            kind,
            r2_key=r2_key,
            preset=preset or f"{kind}-{options.output_width}x{options.output_height}",
            crop_mode=mode.value,
            subtitle_style=json.dumps(style_payload) if style_payload else None,
            status="done",
            duration_ms=int((options.end_s - options.start_s) * 1000),
            size_bytes=size_bytes,
        )
        _finish_job_status(job_id)
        return {"r2_key": r2_key, "size_bytes": size_bytes, "crop_mode": mode.value}
    except JobCanceled:
        return None
    except Exception as exc:
        logger.exception("Render %s untuk job %s segmen %s gagal", kind, job_id, segment_id)
        try:
            _set_render(segment_id, kind, status="failed")
            emit(job_id, "running", "render", 80, f"Render segmen gagal: {exc}")
            _finish_job_status(job_id)
        except JobCanceled:
            pass
        except Exception:
            logger.exception("Gagal mencatat kegagalan render job %s", job_id)
        return None
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
