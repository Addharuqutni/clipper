"""Test kontrak antara klien frontend dan skema FastAPI.

Test ini menjawab pertanyaan yang sebelumnya tidak pernah diperiksa dan
menyebabkan bug nyata: **apakah endpoint yang dipanggil frontend benar-benar
ada di backend?**

Sebelumnya klien memakai `/jobs`, `/uploads/:id/parts`, dan `DELETE /uploads/:id`
— tidak satu pun cocok dengan router yang sebenarnya (`/api/v1/jobs`,
`POST /api/v1/uploads/urls`, `POST /api/v1/uploads/abort`). Tidak ada test yang
menangkapnya karena backend dan frontend diuji terpisah.

Pendekatan: baca berkas klien sebagai teks, ambil setiap literal path, dan
bandingkan dengan daftar path OpenAPI. Ini pemeriksaan statis yang murah dan
menangkap seluruh kelas bug kontrak tanpa perlu menjalankan browser.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from app.main import create_app
from fastapi.testclient import TestClient

#: Akar repo relatif terhadap berkas test ini (apps/api/tests/).
REPO_ROOT = Path(__file__).resolve().parents[3]
CLIENT_FILE = REPO_ROOT / "apps" / "web" / "lib" / "api" / "client.ts"
#: Semua berkas yang memanggil API. multipart.ts dulu tidak diperiksa, dan
#: base URL tanpa /api/v1 di sana membuat setiap unggahan 404.
CALLER_FILES = (CLIENT_FILE, REPO_ROOT / "apps" / "web" / "lib" / "upload" / "multipart.ts")

#: Prefix yang dipakai router backend.
API_PREFIX = "/api/v1"


@pytest.fixture(scope="module")
def api_paths() -> set[str]:
    """Daftar path yang benar-benar didaftarkan backend."""
    with TestClient(create_app()) as client:
        schema = client.get("/openapi.json").json()
    return set(schema["paths"])


@pytest.fixture(scope="module")
def client_source() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in CALLER_FILES)


def _called_paths(source: str) -> set[str]:
    """Ambil literal path yang dipanggil klien.

    Hanya menangkap string yang dimulai dengan ``/`` dan berisi karakter path —
    cukup untuk pola pemanggilan di klien ini, tanpa perlu parser TypeScript.
    """
    raw = re.findall(r'"(/[A-Za-z0-9/_.\-{}\[\]]*)"', source)
    # Template literal: `/jobs/${id}/segments` -> /jobs/{id}/segments
    for template in re.findall(r"`(/[^`]*)`", source):
        raw.append(re.sub(r"\$\{[^}]+\}", "{x}", template).split("?")[0])
    # Abaikan string yang jelas bukan path API (mis. nama MIME, ekstensi).
    return {
        value
        for value in raw
        if not value.endswith((".ts", ".js", ".json"))
        and value not in {"/", "/*", API_PREFIX}
    }


class TestClientMatchesBackend:
    def test_client_file_exists(self) -> None:
        assert CLIENT_FILE.exists(), f"klien API tidak ditemukan di {CLIENT_FILE}"

    def test_every_called_path_exists_in_backend(
        self, client_source: str, api_paths: set[str]
    ) -> None:
        """Inti pemeriksaan: setiap path klien harus ada di OpenAPI."""
        missing: list[str] = []
        for called in sorted(_called_paths(client_source)):
            # Path di klien relatif terhadap API_BASE yang sudah memuat prefix.
            full = f"{API_PREFIX}{called}"
            if full in api_paths:
                continue
            # Dukung path dinamis: /jobs/{id} di klien vs /jobs/{job_id} di spec.
            # Parameter dibandingkan tanpa namanya, karena klien dan spec boleh
            # memakai nama berbeda (id vs job_id) untuk segmen yang sama.
            normalised = re.sub(r"\{[^}]+\}", "{}", full)
            if any(re.sub(r"\{[^}]+\}", "{}", path) == normalised for path in api_paths):
                continue
            missing.append(called)

        assert not missing, (
            "Klien memanggil endpoint yang tidak ada di backend: "
            + ", ".join(missing)
            + ". Ini menyebabkan kegagalan saat runtime yang tidak tertangkap "
            "test backend maupun test frontend secara terpisah."
        )

    def test_client_uses_versioned_prefix(self, client_source: str) -> None:
        # Semua router didaftarkan di bawah /api/v1; klien yang lupa prefix akan
        # mendapat 404 untuk setiap permintaan.
        assert "/api/v1" in client_source, (
            "Klien harus memakai prefix /api/v1; tanpanya semua permintaan 404."
        )

    def test_no_unversioned_paths_remain(self, client_source: str) -> None:
        # Pola lama: request<Job[]>("/jobs") tanpa prefix.
        bare = re.findall(r'request<[^>]*>\(\s*"(/[a-z][^"]*)"', client_source)
        offenders = [path for path in bare if not path.startswith("/")]
        assert not offenders, f"path tanpa prefix masih ada: {offenders}"


class TestClientCoversNewFeatures:
    """Pastikan UI benar-benar punya jalan ke fitur yang baru dibuat."""

    def test_client_exposes_crop_modes(self, client_source: str) -> None:
        assert "/reframe/modes" in client_source
        assert "/reframe/preview" in client_source

    def test_client_exposes_ai_provider(self, client_source: str) -> None:
        assert "/ai/providers" in client_source
        assert "/ai/test" in client_source

    def test_client_exposes_youtube_cookies(self, client_source: str) -> None:
        assert "/youtube/cookies" in client_source
        assert "/youtube/validate-url" in client_source

    @pytest.mark.parametrize(
        "endpoint",
        [
            "/api/v1/reframe/modes",
            "/api/v1/reframe/preview",
            "/api/v1/ai/providers",
            "/api/v1/ai/test",
            "/api/v1/youtube/validate-url",
            "/api/v1/youtube/cookies",
        ],
    )
    def test_new_endpoint_present_in_backend(self, endpoint: str, api_paths: set[str]) -> None:
        assert endpoint in api_paths
