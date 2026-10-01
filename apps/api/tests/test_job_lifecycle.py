"""Alur job end-to-end di API: buat, unggah (resume), proses ulang, batal, hapus, SSE.

Dispatcher di-stub supaya test tidak menjalankan FFmpeg/Whisper; yang diuji
adalah keadaan basis data dan berkas yang dihasilkan API.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

import pytest
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def submitted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Catat task yang dijadwalkan alih-alih menjalankannya."""
    from app.core import dispatch

    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(dispatch, "submit", lambda *args: calls.append(args))
    return calls


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, submitted: list[tuple[Any, ...]]) -> Iterator[TestClient]:
    from app.services import jobs as jobs_service

    async def ai_ready(*_args: object) -> None:
        return None

    monkeypatch.setattr(jobs_service, "ai_config_problem", ai_ready)
    with TestClient(create_app()) as test_client:
        yield test_client


def _create_upload_job(client: TestClient) -> str:
    response = client.post("/api/v1/jobs", json={"source_type": "upload"})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_nama_task_menunjuk_fungsi_worker_yang_ada() -> None:
    from clipper_shared.dispatcher import TASK_POOLS

    for name in TASK_POOLS:
        module, func = name.rsplit(".", 1)
        assert callable(getattr(importlib.import_module(module), func)), name


def test_url_youtube_dikanonikkan_dan_opsi_ditolak(client: TestClient, submitted: list[tuple[Any, ...]]) -> None:
    bad = client.post("/api/v1/jobs", json={"source_type": "youtube", "source_url": "--exec=calc"})
    assert bad.status_code == 422
    assert submitted == []

    ok = client.post("/api/v1/jobs", json={"source_type": "youtube", "source_url": "https://youtu.be/dQw4w9WgXcQ?t=5"})
    assert ok.status_code == 201
    assert ok.json()["source_url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert submitted[-1][0] == "worker_light.tasks.ingest_media"


def test_bahasa_job_bawaan_id_dan_pilihan_tersimpan(client: TestClient) -> None:
    """Bawaan 'id' (deteksi otomatis sering salah bahasa); 'auto' disimpan eksplisit."""
    from clipper_shared.db import get_db_connection

    default = client.post("/api/v1/jobs", json={"source_type": "upload"}).json()
    auto = client.post("/api/v1/jobs", json={"source_type": "upload", "language": "auto"}).json()
    english = client.post("/api/v1/jobs", json={"source_type": "upload", "language": "en"}).json()
    assert (default["language"], auto["language"], english["language"]) == ("id", "auto", "en")

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT language FROM jobs WHERE id = %s", (auto["id"],))
        assert cur.fetchone()[0] == "auto"

    assert client.post("/api/v1/jobs", json={"source_type": "upload", "language": "fr"}).status_code == 422


def test_unggahan_resume_lalu_complete_memulai_ingest(client: TestClient, submitted: list[tuple[Any, ...]]) -> None:
    from app.core.config import settings

    job_id = _create_upload_job(client)
    assert client.get(f"/api/v1/jobs/{job_id}").json()["stage"] == "upload"
    assert submitted == [], "ingest tidak boleh jalan sebelum berkas ada"

    part = settings.UPLOAD_PART_BYTES
    data = b"a" * part + b"b" * 10
    init = client.post("/api/v1/uploads/init", json={"job_id": job_id, "filename": "video.mp4", "size_bytes": len(data)})
    assert init.status_code == 201
    upload_id = init.json()["upload_id"]
    assert init.json()["part_count"] == 2

    assert client.put(f"/api/v1/uploads/{upload_id}/parts/1", content=data[:part]).status_code == 204
    # Potongan salah ukuran ditolak.
    assert client.put(f"/api/v1/uploads/{upload_id}/parts/2", content=b"x").status_code == 422
    assert client.post(f"/api/v1/uploads/{upload_id}/complete").status_code == 409

    resumed = client.post("/api/v1/uploads/init", json={"job_id": job_id, "filename": "video.mp4", "size_bytes": len(data)})
    assert resumed.json()["upload_id"] == upload_id
    assert resumed.json()["received_parts"] == [1]

    assert client.put(f"/api/v1/uploads/{upload_id}/parts/2", content=data[part:]).status_code == 204
    done = client.post(f"/api/v1/uploads/{upload_id}/complete")
    assert done.status_code == 200, done.text
    assert client.get(f"/api/v1/jobs/{job_id}").json()["video_title"] == "video.mp4"
    assert submitted[-1] == ("worker_light.tasks.ingest_media", job_id, "upload", None)

    from clipper_shared import storage as layout
    from clipper_shared.db import get_db_connection

    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT r2_key FROM source_media WHERE job_id = %s", (job_id,))
        (key,) = cur.fetchone()
    assert layout.object_path(layout.RAW, key).read_bytes() == data


def test_unggahan_mengisi_judul_dari_nama_berkas_dan_resume_mempertahankannya(client: TestClient) -> None:
    """Judul job unggahan = nama berkas, ditetapkan saat init dan TIDAK berubah saat resume."""
    job_id = _create_upload_job(client)
    # Job baru belum tahu judul apa pun sampai berkasnya dipilih.
    assert client.get(f"/api/v1/jobs/{job_id}").json()["video_title"] is None

    data = b"a" * 64
    init = client.post(
        "/api/v1/uploads/init",
        json={"job_id": job_id, "filename": "rekaman-podcast.mp4", "size_bytes": len(data)},
    )
    assert init.status_code == 201, init.text
    assert client.get(f"/api/v1/jobs/{job_id}").json()["video_title"] == "rekaman-podcast.mp4"

    # Resume dianggap berkas yang sama (job + ukuran): judul dari manifest
    # dipertahankan walau request resume menyebut nama lain.
    resumed = client.post(
        "/api/v1/uploads/init",
        json={"job_id": job_id, "filename": "nama-lain.mp4", "size_bytes": len(data)},
    )
    assert resumed.status_code == 201, resumed.text
    assert resumed.json()["upload_id"] == init.json()["upload_id"]
    assert client.get(f"/api/v1/jobs/{job_id}").json()["video_title"] == "rekaman-podcast.mp4"


def test_unggahan_baru_setelah_dibatalkan_memperbarui_judul(client: TestClient) -> None:
    """Berkas pengganti tidak boleh tampil di dashboard dengan nama berkas lama."""
    job_id = _create_upload_job(client)
    pertama = client.post(
        "/api/v1/uploads/init",
        json={"job_id": job_id, "filename": "versi-lama.mp4", "size_bytes": 100},
    )
    assert pertama.status_code == 201, pertama.text
    assert client.get(f"/api/v1/jobs/{job_id}").json()["video_title"] == "versi-lama.mp4"

    assert client.delete(f"/api/v1/uploads/{pertama.json()['upload_id']}").status_code == 204

    kedua = client.post(
        "/api/v1/uploads/init",
        json={"job_id": job_id, "filename": "versi-baru.mp4", "size_bytes": 200},
    )
    assert kedua.status_code == 201, kedua.text
    assert kedua.json()["upload_id"] != pertama.json()["upload_id"]
    assert client.get(f"/api/v1/jobs/{job_id}").json()["video_title"] == "versi-baru.mp4"


def test_judul_youtube_dari_metadata_worker_muncul_di_respons_job(client: TestClient) -> None:
    """Judul yang ditulis worker (``YoutubeMetadata.title``) terbaca lewat GET /jobs/{id}."""
    from worker_light import storage

    job_id = client.post(
        "/api/v1/jobs", json={"source_type": "youtube", "source_url": "https://youtu.be/dQw4w9WgXcQ"}
    ).json()["id"]
    # Judul YouTube baru diketahui saat ingest; sebelumnya harus null.
    assert client.get(f"/api/v1/jobs/{job_id}").json()["video_title"] is None

    storage.record_source_media(
        job_id=job_id,
        r2_key="raw/job/video.mp4",
        size_bytes=1024,
        duration_s=60.0,
        width=1920,
        height=1080,
        codec="h264",
        language=None,
        transcript_source=None,
        video_title="Judul Video YouTube",
    )

    assert client.get(f"/api/v1/jobs/{job_id}").json()["video_title"] == "Judul Video YouTube"


def test_job_berjalan_ditandai_gagal_saat_startup_dan_bisa_diulang(submitted: list[tuple[Any, ...]], monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import jobs as jobs_service
    from clipper_shared.db import get_db_connection

    async def ai_ready(*_args: object) -> None:
        return None

    monkeypatch.setattr(jobs_service, "ai_config_problem", ai_ready)
    with TestClient(create_app()) as client:
        job_id = client.post(
            "/api/v1/jobs", json={"source_type": "youtube", "source_url": "https://youtu.be/dQw4w9WgXcQ"}
        ).json()["id"]
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE jobs SET status = 'running', stage = 'transcribe' WHERE id = %s", (job_id,))
        assert client.post(f"/api/v1/jobs/{job_id}/dispatch").status_code == 409

    # "Restart" aplikasi: lifespan baru membereskan job yatim.
    with TestClient(create_app()) as client:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        assert job["status"] == "failed"
        assert "ditutup" in job["error"]
        assert client.post(f"/api/v1/jobs/{job_id}/dispatch").status_code == 200


def test_batal_dan_hapus(client: TestClient) -> None:
    job_id = client.post(
        "/api/v1/jobs", json={"source_type": "youtube", "source_url": "https://youtu.be/dQw4w9WgXcQ"}
    ).json()["id"]
    assert client.post(f"/api/v1/jobs/{job_id}/cancel").json()["status"] == "canceled"

    # Worker yang masih berjalan berhenti di titik periksa berikutnya.
    from clipper_shared.worker_events import JobCanceled, emit

    with pytest.raises(JobCanceled):
        emit(job_id, "running", "ingest", 10, "x")

    assert client.delete(f"/api/v1/jobs/{job_id}").status_code == 204
    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 404


def test_cancel_menandai_render_aktif_canceled_bukan_failed(client: TestClient) -> None:
    """Render yang dihentikan pengguna bukan kegagalan: UI tidak boleh menampilkan 'Render gagal'."""
    import uuid

    from clipper_shared.db import get_db_connection

    job_id = _create_upload_job(client)
    segment_id = str(uuid.uuid4())
    render_ids = {status: str(uuid.uuid4()) for status in ("queued", "running", "done", "failed")}
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE jobs SET status = 'running', stage = 'render' WHERE id = %s", (job_id,))
        cur.execute(
            "INSERT INTO segments (id, job_id, start_s, end_s, score, status) VALUES (%s, %s, 0, 30, 0.9, 'proposed')",
            (segment_id, job_id),
        )
        for status, render_id in render_ids.items():
            cur.execute(
                "INSERT INTO renders (id, segment_id, kind, status, crop_mode) VALUES (%s, %s, 'final', %s, 'face_track')",
                (render_id, segment_id, status),
            )

    assert client.post(f"/api/v1/jobs/{job_id}/cancel").json()["status"] == "canceled"

    statuses = {r["id"]: r["status"] for r in client.get(f"/api/v1/jobs/{job_id}/renders").json()["items"]}
    assert statuses == {
        render_ids["queued"]: "canceled",
        render_ids["running"]: "canceled",
        # Hasil yang sudah final tidak disentuh.
        render_ids["done"]: "done",
        render_ids["failed"]: "failed",
    }


def test_sse_job_selesai_langsung_mengirim_end(client: TestClient) -> None:
    from clipper_shared.db import get_db_connection

    job_id = _create_upload_job(client)
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE jobs SET status = 'done', stage = 'done', progress = 100 WHERE id = %s", (job_id,))
    with client.stream("GET", f"/api/v1/jobs/{job_id}/stream") as response:
        body = response.read().decode()
    assert "event: end" in body
    assert '"status": "done"' in body


def test_cancel_memutus_proses_anak_yang_terdaftar(client: TestClient) -> None:
    """Endpoint cancel harus mematikan proses anak job ini (bukan hanya status).

    Prosesnya adalah interpreter sendiri yang tidur 60 detik — proses anak
    sungguhan, sehingga yang dibuktikan adalah prosesnya benar-benar mati.
    """
    import subprocess
    import sys
    import time

    from clipper_shared.db import get_db_connection

    job_id = _create_upload_job(client)
    # Job harus "aktif" agar cancel menjalankan pemutusan proses.
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE jobs SET status = 'running', stage = 'render' WHERE id = %s", (job_id,))

    from clipper_shared.processes import registered_pids

    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])  # noqa: S603
    from clipper_shared.processes import register

    register(job_id, process)
    try:
        assert registered_pids(job_id) == [process.pid]
        assert process.poll() is None
        assert client.post(f"/api/v1/jobs/{job_id}/cancel").json()["status"] == "canceled"

        deadline = time.monotonic() + 5.0
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert process.poll() is not None, "proses anak masih hidup setelah cancel"
    finally:
        if process.poll() is None:
            process.kill()
        from clipper_shared.processes import unregister

        unregister(job_id, process)
