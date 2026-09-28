"""Job ditolak di awal bila penyedia AI belum siap.

**Mengapa test ini ada.** Tanpa cek ini, job dengan penyedia AI kosong baru
gagal di tahap ``analyze`` — setelah unduhan dan transkripsi yang bisa memakan
puluhan menit. Penolakan harus terjadi SEBELUM baris job dibuat dan sebelum
apa pun dikirim ke antrean.
"""

from __future__ import annotations

import pytest
from app.main import create_app
from fastapi.testclient import TestClient

_ENV_FALLBACK = (
    "AI_PROVIDER_PRESET",
    "CUSTOM_AI_BASE_URL",
    "CUSTOM_AI_MODEL",
    "CUSTOM_AI_API_KEY",
    "GEMINI_API_KEY",
    "ALLOW_PRIVATE_AI_HOST",
)


def test_job_ditolak_sebelum_dibuat_bila_ai_belum_siap(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1 import ai
    from app.core import dispatch

    for name in _ENV_FALLBACK:
        monkeypatch.delenv(name, raising=False)

    async def no_saved_settings(*_args: object) -> None:
        return None

    monkeypatch.setattr(ai, "_load_settings", no_saved_settings)

    dispatched: list[object] = []
    monkeypatch.setattr(dispatch, "dispatch_ingest", lambda *args, **_kw: dispatched.append(args))

    try:
        with TestClient(create_app()) as client:
            before = client.get("/api/v1/jobs").json()["total"]
            response = client.post(
                "/api/v1/jobs",
                json={"source_type": "youtube", "source_url": "https://youtu.be/dQw4w9WgXcQ"},
            )
            after = client.get("/api/v1/jobs").json()["total"]
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"basis data tidak tersedia: {type(exc).__name__}")

    assert response.status_code == 422
    assert "Pengaturan" in response.json()["detail"]
    assert after == before, "job tidak boleh tersimpan bila penyedia AI belum siap"
    assert dispatched == [], "tidak boleh ada yang dikirim ke antrean"
