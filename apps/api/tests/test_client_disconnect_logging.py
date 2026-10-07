"""Traceback "klien memutus koneksi" direkam, bukan dibanjiri ke log.

**Mengapa test ini ada.** Saat browser membatalkan pemutaran klip di tengah
pengiriman berkas (HTTP Range), soket ditutup paksa (RST). Di Windows, transport
Proactor stdlib memanggil ``socket.shutdown()`` tanpa penjagaan, sehingga
``ConnectionResetError`` lolos ke loop exception handler dan tercetak sebagai
traceback — padahal respons 206 sudah terkirim dan tidak ada yang gagal
(bpo-83191; masih ada di Python 3.14.6). Tanpa penanganan ini, log ``[api]``
penuh traceback palsu dan error yang sungguhan jadi sulit terlihat.

Yang dikunci di sini adalah **batas** penanganannya: hanya ``ConnectionResetError``
dan ``ConnectionAbortedError`` yang diturunkan; error lain tetap diteruskan utuh
ke handler sebelumnya, supaya bug sungguhan tidak ikut hilang.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from app import main as main_module


@pytest.fixture
def loop() -> asyncio.AbstractEventLoop:
    return asyncio.new_event_loop()


def test_error_putus_koneksi_diturunkan_ke_debug(
    loop: asyncio.AbstractEventLoop, caplog: pytest.LogCaptureFixture
) -> None:
    diteruskan: list[dict[str, object]] = []
    loop.set_exception_handler(lambda _loop, context: diteruskan.append(context))

    main_module.downgrade_client_disconnect_noise(loop)

    with caplog.at_level(logging.DEBUG, logger=main_module.logger.name):
        loop.call_exception_handler(
            {"message": "Exception in callback", "exception": ConnectionResetError(10054, "RST")}
        )

    assert diteruskan == [], "error koneksi klien tidak boleh sampai ke handler sebelumnya"
    assert "Klien memutus koneksi" in caplog.text
    assert "WinError" not in caplog.text or "Traceback" not in caplog.text


def test_error_lain_tetap_diteruskan(loop: asyncio.AbstractEventLoop) -> None:
    """Bug sungguhan tidak boleh ikut tertelan."""
    diteruskan: list[dict[str, object]] = []
    loop.set_exception_handler(lambda _loop, context: diteruskan.append(context))

    main_module.downgrade_client_disconnect_noise(loop)

    context = {"message": "bug sungguhan", "exception": ValueError("bukan gangguan koneksi")}
    loop.call_exception_handler(context)

    assert diteruskan == [context]


def test_tanpa_handler_sebelumnya_pakai_handler_bawaan(
    loop: asyncio.AbstractEventLoop, caplog: pytest.LogCaptureFixture
) -> None:
    """Tanpa handler sebelumnya, error lain harus tetap muncul lewat handler bawaan."""
    main_module.downgrade_client_disconnect_noise(loop)

    with caplog.at_level(logging.ERROR):
        loop.call_exception_handler(
            {"message": "kesalahan tak terduga", "exception": RuntimeError("boom")}
        )

    assert "kesalahan tak terduga" in caplog.text


def test_lifespan_memasang_penanganan() -> None:
    """Penanganan harus terpasang oleh aplikasi, bukan hanya tersedia sebagai fungsi."""
    from fastapi.testclient import TestClient

    dipasang: list[bool] = []
    asli = main_module.downgrade_client_disconnect_noise

    def _catat(loop: asyncio.AbstractEventLoop) -> None:
        dipasang.append(True)
        asli(loop)

    main_module.downgrade_client_disconnect_noise = _catat  # type: ignore[assignment]
    try:
        with TestClient(main_module.create_app()):
            pass
    finally:
        main_module.downgrade_client_disconnect_noise = asli  # type: ignore[assignment]

    assert dipasang, "lifespan harus memanggil downgrade_client_disconnect_noise"
