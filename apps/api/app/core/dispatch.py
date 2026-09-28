"""Penjadwalan tahap pipeline dari router API.

Pekerjaan berjalan di thread pool proses ini (:mod:`clipper_shared.dispatcher`).
Nama task berupa string agar API tidak mengimpor modul worker saat startup.
"""

from __future__ import annotations

from clipper_shared.dispatcher import submit

INGEST_TASK = "worker_light.tasks.ingest_media"
ANALYZE_TASK = "worker_light.tasks.score_segments"
RENDER_TASK = "worker_render.tasks.render_clip"


def dispatch_ingest(job_id: str, source_type: str, source_url: str | None) -> None:
    """Jadwalkan ingest sebuah job."""
    submit(INGEST_TASK, job_id, source_type, source_url)


def dispatch_rescore(job_id: str) -> None:
    """Jadwalkan ulang analisis AI (transkrip sudah ada)."""
    submit(ANALYZE_TASK, job_id)


def dispatch_render(
    job_id: str,
    segment_id: str,
    kind: str = "preview",
    preset: str | None = None,
    crop_mode: str | None = None,
) -> None:
    """Jadwalkan render satu segmen."""
    submit(RENDER_TASK, job_id, segment_id, kind, preset, crop_mode)
