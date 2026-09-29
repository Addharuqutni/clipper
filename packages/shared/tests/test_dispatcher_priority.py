"""Render yang belum mulai menunggu selama ada transkripsi antre atau berjalan.

Whisper dan FFmpeg sama-sama memakai seluruh core; berjalan bersamaan
memperlambat keduanya. Render yang SUDAH berjalan tidak disela.
"""

from __future__ import annotations

import sys
import threading
import types
from collections.abc import Iterator

import pytest

from clipper_shared import dispatcher

TIMEOUT_S = 5.0


@pytest.fixture
def tasks(monkeypatch: pytest.MonkeyPatch) -> Iterator[types.SimpleNamespace]:
    """Modul task tiruan: transkripsi menunggu dilepas, render mencatat urutan."""
    log: list[str] = []
    release_stt = threading.Event()
    render_started = threading.Event()

    module = types.ModuleType("fake_tasks")

    def transcribe(name: str) -> None:
        log.append(f"stt-start:{name}")
        assert release_stt.wait(TIMEOUT_S)
        log.append(f"stt-end:{name}")

    def render(name: str) -> None:
        log.append(f"render:{name}")
        render_started.set()

    def failing_transcribe(name: str) -> None:
        raise RuntimeError("whisper meledak")

    module.transcribe = transcribe  # type: ignore[attr-defined]
    module.render = render  # type: ignore[attr-defined]
    module.failing_transcribe = failing_transcribe  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_tasks", module)
    monkeypatch.setattr(
        dispatcher,
        "TASK_POOLS",
        {
            "fake_tasks.transcribe": "stt",
            "fake_tasks.failing_transcribe": "stt",
            "fake_tasks.render": "render",
        },
    )
    dispatcher.reset_for_tests()
    yield types.SimpleNamespace(log=log, release_stt=release_stt, render_started=render_started)
    release_stt.set()
    dispatcher.reset_for_tests()


def test_render_menunggu_transkripsi_yang_berjalan(tasks: types.SimpleNamespace) -> None:
    dispatcher.submit("fake_tasks.transcribe", "a")
    dispatcher.submit("fake_tasks.render", "x")

    assert not tasks.render_started.wait(0.3), "render mulai saat transkripsi berjalan"

    tasks.release_stt.set()
    assert tasks.render_started.wait(TIMEOUT_S)
    assert tasks.log == ["stt-start:a", "stt-end:a", "render:x"]


def test_render_jalan_langsung_tanpa_transkripsi(tasks: types.SimpleNamespace) -> None:
    dispatcher.submit("fake_tasks.render", "x")
    assert tasks.render_started.wait(TIMEOUT_S)


def test_transkripsi_gagal_tidak_mengunci_render_selamanya(tasks: types.SimpleNamespace) -> None:
    dispatcher.submit("fake_tasks.failing_transcribe", "a")
    dispatcher.submit("fake_tasks.render", "x")
    assert tasks.render_started.wait(TIMEOUT_S)


def test_shutdown_melepas_render_yang_menunggu(tasks: types.SimpleNamespace) -> None:
    dispatcher.submit("fake_tasks.transcribe", "a")
    dispatcher.submit("fake_tasks.transcribe", "b")  # antre di pool stt (1 slot)

    waiter = threading.Thread(target=dispatcher.wait_for_stt_idle, daemon=True)
    waiter.start()
    waiter.join(0.2)
    assert waiter.is_alive()

    dispatcher.shutdown()
    waiter.join(TIMEOUT_S)
    assert not waiter.is_alive()


def test_render_job_dibatalkan_berhenti_menunggu(
    tasks: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Render job yang dibatalkan tidak menahan thread render sampai antrean STT kosong."""
    from clipper_shared import worker_events

    monkeypatch.setattr(dispatcher, "STT_WAIT_POLL_S", 0.05)
    monkeypatch.setattr(worker_events, "is_canceled", lambda job_id: job_id == "job-batal")
    dispatcher.submit("fake_tasks.transcribe", "a")

    waiter = threading.Thread(target=dispatcher.wait_for_stt_idle, args=("job-batal",), daemon=True)
    waiter.start()
    waiter.join(TIMEOUT_S)
    assert not waiter.is_alive()
    assert tasks.log == ["stt-start:a"]
