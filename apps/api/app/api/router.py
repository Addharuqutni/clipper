"""Router v1.

Pengelompokan prefix:

* ``/uploads``    — unggahan berkas lokal per potongan (resume).
* ``/jobs``       — siklus hidup job + SSE progres.
* ``/reframe``    — mode crop dan pratinjau geometri.
* ``/ai``         — preset penyedia AI dan uji koneksi.
* ``/youtube``    — validasi URL, cookies, dan pemilihan trek subtitle.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    ai,
    caption,
    fonts,
    jobs,
    overlay,
    reframe,
    transcript,
    uploads,
    youtube,
)

api_router = APIRouter()
api_router.include_router(uploads.router, prefix="/uploads", tags=["uploads"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
api_router.include_router(transcript.router, prefix="/jobs", tags=["transcript"])
api_router.include_router(caption.router, prefix="/captions", tags=["captions"])
api_router.include_router(fonts.router, prefix="/fonts", tags=["fonts"])
api_router.include_router(overlay.router, prefix="/overlays", tags=["overlays"])
api_router.include_router(reframe.router, prefix="/reframe", tags=["reframe"])
api_router.include_router(ai.router, prefix="/ai", tags=["ai"])
api_router.include_router(youtube.router, prefix="/youtube", tags=["youtube"])

__all__ = ["api_router"]
