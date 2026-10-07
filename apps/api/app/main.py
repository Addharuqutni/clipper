"""Entrypoint FastAPI.

Satu proses menjalankan API dan seluruh pipeline: worker-light dan
worker-render berjalan di thread pool proses ini (``clipper_shared.dispatcher``).
Tidak ada autentikasi; API hanya boleh didengarkan di 127.0.0.1.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# Worker dan paket bersama diimpor dari source tree (tanpa instalasi terpisah).
_repo_root = Path(__file__).resolve().parents[3]
for _pkg in (
    _repo_root / "apps" / "worker-light",
    _repo_root / "apps" / "worker-render",
    _repo_root / "packages" / "shared" / "src",
):
    if str(_pkg) not in sys.path and _pkg.is_dir():
        sys.path.insert(0, str(_pkg))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.api.router import api_router
from app.core.config import settings
from app.db.session import check_database, dispose_engine, init_db_schema
from app.services.errors import ServiceError

logger = logging.getLogger(__name__)

#: Exception yang WAJAR terjadi saat klien memutus koneksi streaming dengan
#: paksa (RST) — mis. browser membatalkan pemutaran klip di tengah pengiriman
#: berkas lewat HTTP Range.
#:
#: Di Windows, ``_ProactorBasePipeTransport._call_connection_lost`` memanggil
#: ``socket.shutdown()`` TANPA penjagaan (``asyncio/proactor_events.py``), lalu
#: exception-nya lolos ke loop exception handler dan tercetak sebagai traceback
#: meski respons sudah terkirim (bpo-83191; masih ada di Python 3.14.6). Ini
#: bukan kegagalan aplikasi, dan traceback-nya hanya mengotori log serta
#: menutupi error yang sungguhan.
_CLIENT_DISCONNECT_ERRORS = (ConnectionResetError, ConnectionAbortedError)

def downgrade_client_disconnect_noise(loop: asyncio.AbstractEventLoop) -> None:
    """Turunkan error "klien memutus koneksi" ke level debug; sisanya diteruskan.

    Hanya dua jenis ``OSError`` ini yang ditelan, dan hanya di loop exception
    handler — error lain tetap sampai ke handler sebelumnya (atau ke handler
    bawaan asyncio) dengan format aslinya. Lihat
    :data:`_CLIENT_DISCONNECT_ERRORS`.
    """
    previous = loop.get_exception_handler()

    def _handler(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        if isinstance(exc, _CLIENT_DISCONNECT_ERRORS):
            logger.debug("Klien memutus koneksi saat streaming: %s", exc)
            return
        if previous is not None:
            previous(_loop, context)
        else:
            _loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)


async def ensure_local_user_row() -> None:
    """Pastikan baris pengguna lokal ada sebelum permintaan pertama.

    Kegagalan di sini TIDAK menggagalkan startup: ``get_current_user`` akan
    mencoba lagi pada permintaan berikutnya.
    """
    from app.core.identity import ensure_local_user
    from app.db.session import SessionLocal

    try:
        async with SessionLocal() as session:
            await ensure_local_user(session)
            await session.commit()
    except Exception:  # noqa: BLE001 - startup tidak boleh gagal karena ini
        logger.warning("Tidak dapat menyiapkan baris pengguna lokal saat startup.", exc_info=True)


async def _maintenance_loop() -> None:
    """Hapus media mentah kedaluwarsa secara berkala (di thread, bukan event loop)."""
    from clipper_shared.maintenance import purge_expired_raw_media

    while True:
        try:
            await asyncio.to_thread(purge_expired_raw_media)
        except Exception:
            logger.exception("Pemeliharaan berkala gagal")
        await asyncio.sleep(settings.MAINTENANCE_INTERVAL_S)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: skema DB, bereskan job yatim dari sesi sebelumnya, lalu pemeliharaan."""
    from clipper_shared import dispatcher
    from clipper_shared.maintenance import reconcile_interrupted_jobs, sweep_workspaces

    logger.info("ClipperAI API starting (env=%s version=%s)", settings.ENV, __version__)
    # Sebelum apa pun menyajikan berkas: redam traceback "klien memutus koneksi"
    # yang berasal dari stdlib asyncio, bukan dari kode aplikasi.
    downgrade_client_disconnect_noise(asyncio.get_running_loop())
    await init_db_schema()
    await ensure_local_user_row()
    # Belum ada task yang berjalan di proses baru ini, jadi semua job
    # queued/running dan semua ruang kerja sementara pasti sisa sesi lalu.
    await asyncio.to_thread(reconcile_interrupted_jobs)
    await asyncio.to_thread(sweep_workspaces)
    maintenance = asyncio.create_task(_maintenance_loop())
    yield
    maintenance.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await maintenance
    dispatcher.shutdown()
    await dispose_engine()
    logger.info("ClipperAI API stopped")


def create_app() -> FastAPI:
    """App factory — dipakai uvicorn dan test suite."""
    application = FastAPI(
        title=settings.APP_NAME,
        version=__version__,
        description="ClipperAI backend lokal: job, unggahan, render, progres SSE.",
        docs_url="/docs" if settings.ENV != "prod" else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.ALLOWED_HOSTS)

    @application.exception_handler(ServiceError)
    async def _service_error_handler(_request: Request, exc: ServiceError) -> JSONResponse:
        """Kesalahan domain service → respons HTTP yang sama dengan sebelumnya.

        Service tidak mengimpor FastAPI; satu handler ini menjaga kode status
        dan bentuk badan respons identik dengan saat aturan berada di route.
        """
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    application.include_router(api_router, prefix=settings.API_V1_PREFIX)

    @application.get("/health", tags=["health"])
    async def health() -> dict[str, Any]:
        """Liveness: tidak menyentuh basis data."""
        return {"status": "ok", "service": "api", "version": __version__}

    @application.get("/health/ready", tags=["health"])
    async def health_ready() -> dict[str, Any]:
        """Readiness: basis data dapat dibaca."""
        db_ok = await check_database()
        return {"status": "ready" if db_ok else "degraded", "checks": {"database": db_ok}}

    return application


app = create_app()
