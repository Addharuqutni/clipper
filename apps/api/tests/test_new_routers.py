"""Test router baru: reframe, ai, youtube.

Yang diuji bukan sekadar "endpoint mengembalikan 200", melainkan bahwa kontrak
endpoint benar-benar sesuai harapan UI:

* mode crop yang dilaporkan konsisten dengan validator backend;
* pratinjau geometri memberi angka bar yang benar;
* cookies TIDAK PERNAH dikembalikan isinya;
* platform yang belum tersedia tidak pernah dilaporkan sebagai tersedia.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    """Klien uji tanpa lifespan — test ini tidak menyentuh DB/Redis."""
    with TestClient(create_app()) as test_client:
        yield test_client


class TestReframeRouter:
    def test_lists_three_modes(self, client: TestClient) -> None:
        response = client.get("/api/v1/reframe/modes")
        assert response.status_code == 200
        body = response.json()
        ids = {mode["id"] for mode in body["modes"]}
        assert ids == {"face_track", "black_bars", "blurred_fill"}
        assert body["default"] == "face_track"

    def test_mode_ids_match_backend_validator(self, client: TestClient) -> None:
        # Kalau daftar ini melenceng dari yang diterima mesin render, UI akan
        # menawarkan mode yang lalu ditolak worker.
        from clipper_shared.reframe import CropMode

        response = client.get("/api/v1/reframe/modes")
        served = {mode["id"] for mode in response.json()["modes"]}
        assert served == {mode.value for mode in CropMode}

    def test_face_track_flagged_as_requiring_detection(self, client: TestClient) -> None:
        body = client.get("/api/v1/reframe/modes").json()
        by_id = {mode["id"]: mode for mode in body["modes"]}
        assert by_id["face_track"]["requires_face_detection"] is True
        assert by_id["black_bars"]["requires_face_detection"] is False
        # Mode berbar mempertahankan seluruh bingkai; face tracking memotong.
        assert by_id["black_bars"]["preserves_full_frame"] is True
        assert by_id["face_track"]["preserves_full_frame"] is False

    def test_every_mode_has_description(self, client: TestClient) -> None:
        body = client.get("/api/v1/reframe/modes").json()
        for mode in body["modes"]:
            assert mode["description"]

    def test_preview_landscape_gives_bars(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/reframe/preview",
            json={"source_width": 1920, "source_height": 1080, "mode": "black_bars"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["scaled_width"] == 1080
        assert body["bar_height_top"] > 0
        assert body["bar_height_bottom"] > 0
        # Bar harus hampir sama tinggi; selisih maksimum 1 piksel setelah
        # pembulatan ke kelipatan 4.
        assert abs(body["bar_height_top"] - body["bar_height_bottom"]) <= 1

    def test_preview_bars_are_even(self, client: TestClient) -> None:
        body = client.post(
            "/api/v1/reframe/preview",
            json={"source_width": 1920, "source_height": 1080, "mode": "black_bars"},
        ).json()
        # H.264 + yuv420p mensyaratkan dimensi genap.
        assert body["scaled_height"] % 2 == 0
        assert body["bar_height_top"] % 2 == 0

    def test_preview_face_track_has_no_bars(self, client: TestClient) -> None:
        body = client.post(
            "/api/v1/reframe/preview",
            json={"source_width": 1920, "source_height": 1080, "mode": "face_track"},
        ).json()
        assert body["bar_height_top"] == 0
        assert body["bar_height_bottom"] == 0

    def test_preview_warns_on_tall_source(self, client: TestClient) -> None:
        # Sumber lebih tinggi dari 9:16 akan menghasilkan bar kiri-kanan.
        body = client.post(
            "/api/v1/reframe/preview",
            json={"source_width": 720, "source_height": 1920, "mode": "blurred_fill"},
        ).json()
        assert body["bar_width_left"] > 0
        assert body["warning"]

    def test_preview_rejects_invalid_dimensions(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/reframe/preview",
            json={"source_width": 0, "source_height": 1080, "mode": "black_bars"},
        )
        assert response.status_code == 422

    def test_preview_get_variant(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/reframe/modes/black_bars/preview",
            params={"source_width": 1920, "source_height": 1080},
        )
        assert response.status_code == 200
        assert response.json()["mode"] == "black_bars"

    def test_filter_chain_present_and_mode_specific(self, client: TestClient) -> None:
        bars = client.post(
            "/api/v1/reframe/preview",
            json={"source_width": 1920, "source_height": 1080, "mode": "black_bars"},
        ).json()
        blur = client.post(
            "/api/v1/reframe/preview",
            json={"source_width": 1920, "source_height": 1080, "mode": "blurred_fill"},
        ).json()
        assert "pad=" in bars["filter_chain"]
        assert "boxblur=" in blur["filter_chain"]


class TestYoutubeRouter:
    def test_accepts_watch_url(self, client: TestClient) -> None:
        body = client.post(
            "/api/v1/youtube/validate-url",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        ).json()
        assert body["valid"] is True
        assert body["video_id"] == "dQw4w9WgXcQ"

    def test_accepts_short_link(self, client: TestClient) -> None:
        body = client.post(
            "/api/v1/youtube/validate-url",
            json={"url": "https://youtu.be/dQw4w9WgXcQ"},
        ).json()
        assert body["valid"] is True
        assert body["video_id"] == "dQw4w9WgXcQ"

    def test_detects_shorts(self, client: TestClient) -> None:
        body = client.post(
            "/api/v1/youtube/validate-url",
            json={"url": "https://youtube.com/shorts/dQw4w9WgXcQ"},
        ).json()
        assert body["valid"] is True
        assert body["is_short"] is True

    def test_rejects_non_youtube_host(self, client: TestClient) -> None:
        body = client.post(
            "/api/v1/youtube/validate-url",
            json={"url": "https://vimeo.com/12345"},
        ).json()
        assert body["valid"] is False

    def test_rejects_malformed_video_id(self, client: TestClient) -> None:
        body = client.post(
            "/api/v1/youtube/validate-url",
            json={"url": "https://www.youtube.com/watch?v=terlalupendek"},
        ).json()
        assert body["valid"] is False

    def test_invalid_url_message_is_helpful(self, client: TestClient) -> None:
        body = client.post(
            "/api/v1/youtube/validate-url",
            json={"url": "bukan-url"},
        ).json()
        assert "youtube.com/watch" in body["message"] or "youtu.be" in body["message"]

    def test_cookie_requirements_are_public(self, client: TestClient) -> None:
        response = client.get("/api/v1/youtube/cookie-requirements")
        assert response.status_code == 200
        body = response.json()
        assert "SID" in body["required_any_of"]
        assert body["max_file_bytes"] > 0

    def test_valid_cookies_accepted(self, client: TestClient) -> None:
        content = (
            "# Netscape HTTP Cookie File\n"
            ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tabc\n"
            ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tLOGIN_INFO\txyz\n"
        )
        response = client.post(
            "/api/v1/youtube/cookies",
            files={"file": ("cookies.txt", content, "text/plain")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is True
        assert "SID" in body["found"]

    def test_cookie_content_never_echoed(self, client: TestClient) -> None:
        # Ini batas keamanan yang paling penting di router ini: cookie YouTube
        # setara kredensial sesi penuh, jadi isinya tidak boleh pernah kembali
        # dalam respons.
        secret = "RAHASIA_JANGAN_BOCOR_12345"
        content = (
            "# Netscape HTTP Cookie File\n"
            f".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\t{secret}\n"
        )
        response = client.post(
            "/api/v1/youtube/cookies",
            files={"file": ("cookies.txt", content, "text/plain")},
        )
        assert response.status_code == 200
        assert secret not in response.text

    def test_invalid_cookies_rejected_with_reason(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/youtube/cookies",
            files={"file": ("cookies.txt", "bukan cookies", "text/plain")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is False
        assert body["message"]

    def test_oversized_cookie_file_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/youtube/cookies",
            files={"file": ("cookies.txt", "x" * (600 * 1024), "text/plain")},
        )
        assert response.status_code == 413


class TestAiRouter:
    def test_lists_providers(self, client: TestClient) -> None:
        response = client.get("/api/v1/ai/providers")
        assert response.status_code == 200
        presets = response.json()["presets"]
        ids = {preset["id"] for preset in presets}
        assert {"gemini", "openai", "ollama", "custom"} <= ids

    def test_provider_entries_have_labels(self, client: TestClient) -> None:
        presets = client.get("/api/v1/ai/providers").json()["presets"]
        for preset in presets:
            assert preset["label"]
            assert "requires_api_key" in preset

    def test_invalid_config_returns_400_not_500(self, client: TestClient) -> None:
        # Kesalahan konfigurasi adalah salah pengguna; 500 akan menyesatkan dan
        # menyembunyikan pesan yang bisa ditindaklanjuti.
        #
        # PENTING: endpoint ini sengaja memakai kembali kunci API TERSIMPAN bila
        # field kunci kosong (lihat `test_provider`). Pada basis data dev yang
        # sudah berisi pengaturan, mengirim `api_key: ""` karena itu SAH dan
        # akan menghubungi penyedia sungguhan — bukan 400. Agar test ini
        # mengukur validasi konfigurasi dan bukan isi basis data, penimpaan
        # `base_url` sekaligus `model` dipakai: keduanya membuat kunci tersimpan
        # tidak lagi berlaku, sehingga validasi `requires_api_key` benar-benar
        # berjalan.
        response = client.post(
            "/api/v1/ai/test",
            json={
                "preset": "openai",
                "api_key": "",
                "base_url": "https://contoh.example.com/v1",
                "model": "contoh-model",
            },
        )
        assert response.status_code == 400
        assert "API key" in response.json()["detail"]

    def test_ssrf_blocked_for_private_host(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/ai/test",
            json={
                "preset": "custom",
                "base_url": "http://postgres:5432/v1",
                "model": "x",
                "api_key": "k",
            },
        )
        assert response.status_code == 400
        assert "privat" in response.json()["detail"].lower()

    def test_custom_requires_model(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/ai/test",
            json={
                "preset": "custom",
                "base_url": "https://contoh.example.com/v1",
                "model": "",
                "api_key": "k",
            },
        )
        assert response.status_code == 400
        assert "model" in response.json()["detail"].lower()


class TestOpenApiSchema:
    def test_new_endpoints_documented(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        paths = set(schema["paths"])
        for expected in (
            "/api/v1/reframe/modes",
            "/api/v1/reframe/preview",
            "/api/v1/ai/providers",
            "/api/v1/ai/test",
            "/api/v1/youtube/validate-url",
            "/api/v1/youtube/cookies",
        ):
            assert expected in paths, f"{expected} tidak terdaftar di OpenAPI"
