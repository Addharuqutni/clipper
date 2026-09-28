"""Smoke test ``/health``, ``/health/ready``, OpenAPI, dan penjagaan host."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from app import main as main_module
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def test_health_ok(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body == {"status": "ok", "service": "api", "version": body["version"]}


def test_ready_memeriksa_basis_data(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert client.get("/health/ready").json() == {"status": "ready", "checks": {"database": True}}

    async def _down() -> bool:
        return False

    monkeypatch.setattr(main_module, "check_database", _down)
    assert client.get("/health/ready").json()["status"] == "degraded"


def test_openapi_memuat_endpoint_inti(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for expected in (
        "/api/v1/uploads/init",
        "/api/v1/uploads/{upload_id}/parts/{part_number}",
        "/api/v1/jobs",
        "/api/v1/jobs/{job_id}/stream",
        "/api/v1/jobs/{job_id}/cancel",
        "/api/v1/jobs/{job_id}/rescore",
        "/api/v1/jobs/{job_id}/segments/{segment_id}/social-caption",
    ):
        assert expected in paths, f"endpoint {expected} tidak terdaftar"
    # Desain satu pengguna tanpa login: endpoint auth tidak boleh muncul lagi.
    assert not [p for p in paths if p.startswith("/api/v1/auth")]


def test_host_asing_ditolak(client: TestClient) -> None:
    """DNS rebinding: halaman jahat yang mengarahkan domainnya ke 127.0.0.1 ditolak."""
    assert client.get("/health", headers={"host": "evil.example"}).status_code == 400
