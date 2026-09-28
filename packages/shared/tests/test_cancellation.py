"""Test pembatalan job: proses anak benar-benar diputus, bukan menunggu selesai.

**Mengapa test ini ada.** Sebelumnya pembatalan hanya berhenti di titik periksa
``emit`` berikutnya: FFmpeg (render/burn/extract), yt-dlp, dan Whisper berjalan
sampai selesai. Utang teknis itu ditutup oleh :mod:`clipper_shared.processes` —
setiap peluncuran proses wajib lewat helper itu agar terdaftar per job.

Test di sini menjalankan proses anak SUNGGUHAN (interpreter sendiri dengan
``time.sleep``), bukan mock: yang harus dibuktikan adalah prosesnya benar-benar
mati, bukan bahwa sebuah fungsi dipanggil.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


def _setup_db(tmp_path: Path) -> None:
    """Arahkan DB dan penyimpanan ke direktori test SEBELUM modul dibaca."""
    os.environ["DATABASE_URL"] = ""
    os.environ["LOCAL_STORAGE_DIR"] = str(tmp_path)
    os.environ["WORKER_WORKSPACE_DIR"] = str(tmp_path / "work")


@pytest.fixture
def job_id(tmp_path: Path) -> str:
    """Job 'running' sungguhan di SQLite test, plus fungsi pembatalannya."""
    import clipper_shared.db as dbmod

    dbmod.get_db_connection  # noqa: B018 — pastikan modul termuat

    from clipper_shared.db import get_db_connection, utc_now

    _setup_db(tmp_path)

    # Skema minimal: hanya tabel jobs yang dipakai jalur pembatalan.
    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                status TEXT NOT NULL DEFAULT 'queued',
                stage TEXT,
                progress INTEGER DEFAULT 0,
                error TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        cursor.execute(
            "INSERT INTO jobs (id, status, stage, created_at, updated_at) VALUES (%s, 'running', 'render', %s, %s)",
            ("job-cancel-1", utc_now(), utc_now()),
        )
    return "job-cancel-1"


def _cancel_job(job_id: str) -> None:
    """Tiru API: status canceled tersimpan, lalu proses anak dimatikan."""
    from clipper_shared.db import get_db_connection, utc_now
    from clipper_shared.processes import terminate_job
    from clipper_shared.worker_events import is_canceled

    with get_db_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE jobs SET status = 'canceled', updated_at = %s WHERE id = %s", (utc_now(), job_id)
        )
    assert is_canceled(job_id), "status canceled harus terlihat worker"
    terminate_job(job_id)


def _pid_alive(pid: int) -> bool:
    """Benar bila proses masih ada.

    ``process.poll()`` hanya melihat proses yang kita pegang; pada Windows
    anak proses (pohon) perlu diperiksa langsung — itulah yang dibuktikan
    ``taskkill /T``.
    """
    if os.name == "nt":
        tasklist = shutil.which("tasklist") or "tasklist"
        result = subprocess.run(  # noqa: S603
            # Argumen dibangun internal (PID dari proses kita sendiri); tidak ada
            # masukan pengguna. tasklist bukan binary jahat — ia satu-satunya cara
            # memeriksa proses di luar pohon milik proses test.
            [tasklist, "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class TestProcessTermination:
    """Proses anak yang terdaftar harus mati dalam hitungan detik."""

    def test_cancel_mematikan_proses_anak(self, job_id: str) -> None:
        from clipper_shared.processes import run_process

        # Proses panjang yang tidak akan selesai sendiri.
        command = [sys.executable, "-c", "import time; time.sleep(60)"]
        result: dict[str, object] = {}

        def _run() -> None:
            from clipper_shared.worker_events import JobCanceled

            try:
                run_process(command, job_id=job_id, capture_output=True, check=True)
                result["outcome"] = "selesai"
            except JobCanceled:
                result["outcome"] = "canceled"
            except Exception as exc:  # noqa: BLE001 — dilaporkan sebagai kegagalan test
                result["outcome"] = f"error: {exc!r}"

        worker = threading.Thread(target=_run, name="test-cancel-worker")
        worker.start()
        time.sleep(1.0)  # beri waktu proses anak benar-benar mulai

        from clipper_shared.processes import registered_pids

        pids = registered_pids(job_id)
        assert pids, "proses anak harus terdaftar untuk job ini"
        assert all(_pid_alive(pid) for pid in pids), "proses anak harus hidup sebelum dibatalkan"

        started = time.monotonic()
        _cancel_job(job_id)
        worker.join(timeout=15.0)
        elapsed = time.monotonic() - started

        assert not worker.is_alive(), "worker tidak berhenti setelah pembatalan"
        assert result.get("outcome") == "canceled", f"hasil tak terduga: {result.get('outcome')}"
        # "Dalam beberapa detik", bukan setelah sleep 60.
        assert elapsed < 10.0, f"pembatalan butuh {elapsed:.1f} s"
        # Proses benar-benar mati, bukan sekadar dilepas worker.
        for pid in pids:
            deadline = time.monotonic() + 5.0
            while _pid_alive(pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            assert not _pid_alive(pid), f"proses {pid} masih hidup setelah pembatalan"


class TestWhisperStopBetweenSegments:
    """Whisper lokal harus berhenti antar segmen, bukan menunggu rekaman selesai.

    ``faster_whisper`` mengembalikan generator segmen yang malas: satu segmen
    memakan waktu nyata yang sama dengan durasinya. Pemeriksaan dilakukan di
    dalam loop itu; test di sini menirukan generator tersebut dengan segmen
    palsu agar perilakunya terbukti tanpa model Whisper sungguhan.
    """

    def test_berhenti_sebelum_segmen_berikutnya(self, job_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
        from clipper_shared import stt
        from clipper_shared.worker_events import JobCanceled

        segments_seen: list[int] = []

        class _Segment:
            def __init__(self, text: str) -> None:
                self.text = text
                self.start = 0.0
                self.end = 1.0
                self.words: list[object] = []

        def _fake_segments() -> object:
            index = 0
            while True:
                segments_seen.append(index)
                if index == 2:
                    # Segmen ke-3: tirukan pembatalan di tengah jalan.
                    from clipper_shared.db import get_db_connection, utc_now

                    with get_db_connection() as connection, connection.cursor() as cursor:
                        cursor.execute(
                            "UPDATE jobs SET status = 'canceled', updated_at = %s WHERE id = %s",
                            (utc_now(), job_id),
                        )
                index += 1
                yield _Segment(f"segmen {index}")

        class _Info:
            language = "id"
            duration = 100.0

        class _Model:
            def transcribe(self, *_args: object, **_kwargs: object) -> tuple[object, object]:
                return _fake_segments(), _Info()

        transcriber = stt.FasterWhisperLocal(job_id=job_id)
        monkeypatch.setattr(transcriber, "_load_model", lambda: _Model())

        with pytest.raises(JobCanceled):
            transcriber.transcribe("audio.wav", language=None)

        # Berhenti tepat setelah pembatalan terlihat — bukan setelah semua segmen.
        assert len(segments_seen) == 3, f"segmen terbaca: {len(segments_seen)}"


class TestCheckSemantics:
    """``check=True`` harus tetap berperilaku seperti ``subprocess.run``.

    ``run_process`` menerima kwargs gaya ``subprocess.run``; ``check`` sempat
    dibuang diam-diam, sehingga pemanggil yang bergantung pada CalledProcessError
    (ffmpeg/yt-dlp gagal) tidak pernah menerimanya. Test ini mengunci kontraknya.
    """

    def test_check_true_melempar_called_process_error(self) -> None:
        import subprocess as sp

        from clipper_shared.processes import run_process

        with pytest.raises(sp.CalledProcessError) as excinfo:
            run_process(
                [sys.executable, "-c", "import sys; sys.exit(3)"],
                check=True,
                capture_output=True,
            )
        assert excinfo.value.returncode == 3
        assert excinfo.value.cmd[:1] == [sys.executable]

    def test_check_false_mengembalikan_hasil(self) -> None:
        from clipper_shared.processes import run_process

        result = run_process(
            [sys.executable, "-c", "import sys; sys.exit(3)"],
            capture_output=True,
        )
        assert result.returncode == 3
