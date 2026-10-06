"""Test endpoint transkrip: GET transcript & PATCH word update."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from app.core import dispatch
from app.db.session import SessionLocal
from app.main import create_app
from app.models.transcript import Transcript
from app.services import jobs as jobs_service
from fastapi.testclient import TestClient


@pytest.fixture
def submitted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(dispatch, "submit", lambda *args: calls.append(args))
    return calls


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, submitted: list[tuple[Any, ...]]) -> Iterator[TestClient]:
    async def ai_ready(*_args: object) -> None:
        return None

    monkeypatch.setattr(jobs_service, "ai_config_problem", ai_ready)
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.mark.anyio
async def test_get_and_patch_transcript(client: TestClient) -> None:
    # 1. Buat job unggahan lewat endpoint resmi
    res_job = client.post("/api/v1/jobs", json={"source_type": "upload"})
    assert res_job.status_code == 201
    job_id = res_job.json()["id"]

    # 2. Sisipkan baris transcript langsung ke DB
    raw_words = [
        {"text": "halo", "start": 0.0, "end": 0.5},
        {"text": "dunia", "start": 0.6, "end": 1.2},
    ]
    async with SessionLocal() as db:
        transcript = Transcript(
            id=uuid4(),
            job_id=job_id,
            language="id",
            words=raw_words,
            full_text="halo dunia",
            model_used="youtube-subs",
        )
        db.add(transcript)
        await db.commit()

    # 3. GET transcript (harus dikanonikalisasi ke start_s)
    res = client.get(f"/api/v1/jobs/{job_id}/transcript")
    assert res.status_code == 200
    data = res.json()
    assert len(data["words"]) == 2
    assert data["words"][0]["text"] == "halo"
    assert data["words"][0]["start_s"] == 0.0
    assert data["words"][1]["text"] == "dunia"
    assert data["words"][1]["start_s"] == 0.6

    # 4. PATCH update word
    patch_res = client.patch(
        f"/api/v1/jobs/{job_id}/transcript/words/1",
        json={"text": "semua", "expected_text": "dunia"},
    )
    assert patch_res.status_code == 200
    patch_data = patch_res.json()
    assert patch_data["removed"] is False
    assert patch_data["word_count"] == 2

    # 5. GET kembali untuk memverifikasi full_text dan kata terupdate
    res2 = client.get(f"/api/v1/jobs/{job_id}/transcript")
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["full_text"] == "halo semua"
    assert data2["words"][1]["text"] == "semua"

    # 6. PATCH conflict error
    conflict_res = client.patch(
        f"/api/v1/jobs/{job_id}/transcript/words/1",
        json={"text": "gagal", "expected_text": "dunia"},
    )
    assert conflict_res.status_code == 409

    # 7. PATCH out of bounds
    oob_res = client.patch(
        f"/api/v1/jobs/{job_id}/transcript/words/99",
        json={"text": "gagal"},
    )
    assert oob_res.status_code == 422
