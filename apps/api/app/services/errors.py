"""Kesalahan domain service, dipetakan route ke respons HTTP.

Service TIDAK mengimpor FastAPI, jadi aturan bisnis dapat diuji langsung tanpa
klien HTTP. Pemetaan ke ``HTTPException``/JSONResponse dilakukan di lapisan
HTTP: handler terdaftar di :func:`app.main.create_app` memakai ``status_code``
dan ``detail`` dari sini, sehingga kode status dan pesan tetap sama persis
seperti saat aturan itu masih berada di route.
"""

from __future__ import annotations

from fastapi import status


class ServiceError(Exception):
    """Kesalahan domain yang layak ditampilkan ke pengguna."""

    status_code: int = status.HTTP_400_BAD_REQUEST

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class BadRequestError(ServiceError):
    """Permintaan tidak sah secara konfigurasi (HTTP 400)."""

    status_code = status.HTTP_400_BAD_REQUEST


class NotFoundError(ServiceError):
    """Sumber daya tidak ada atau bukan milik pengguna (HTTP 404)."""

    status_code = status.HTTP_404_NOT_FOUND


class ConflictError(ServiceError):
    """Keadaan saat ini tidak memungkinkan operasi (HTTP 409)."""

    status_code = status.HTTP_409_CONFLICT


class UnprocessableError(ServiceError):
    """Nilai yang dikirim tidak dapat diproses (HTTP 422)."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
