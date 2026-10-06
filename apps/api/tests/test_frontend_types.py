"""Kontrak bentuk data antara backend dan frontend.

Test ini menjawab pertanyaan yang sebelumnya tidak pernah diperiksa: **apakah
field respons backend benar-benar sama dengan tipe TypeScript yang dikonsumsi
frontend?**

``test_frontend_contract.py`` memeriksa *path* endpoint. Itu menangkap router
yang hilang, tetapi tidak menangkap field yang hilang: ``video_title`` pernah
ditambahkan ke kedua sisi dengan tangan, dan tidak ada test yang akan gagal bila
salah satu sisi tertinggal. Akibatnya baru terlihat sebagai ``undefined`` di
browser.

Tiga hal yang dikunci di sini:

1. **Field respons.** Nama field dan wajib/opsionalnya (``?`` di TypeScript vs
   ``required`` di OpenAPI) harus sama untuk setiap pasangan model↔interface.
2. **Nilai union.** ``JobStatus``, ``SegmentStatus``, ``RenderKind``, dan
   ``CropMode`` di TypeScript harus sama persis dengan CHECK constraint di basis
   data — sumber kebenaran yang sebenarnya menolak nilai salah saat runtime.
3. **Payload SSE.** ``JobEvent`` bukan cermin sebuah response model, melainkan
   payload yang dipublikasikan worker; test ini menangkapnya dari perilaku
   ``emit`` supaya perubahan bentuk payload tidak lewat tanpa terlihat.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import CheckConstraint

REPO_ROOT = Path(__file__).resolve().parents[3]
TYPES_FILE = REPO_ROOT / "apps" / "web" / "lib" / "types.ts"

#: Pasangan (nama schema OpenAPI, nama interface TypeScript).
#:
#: ``JobEventResponse`` sengaja tidak dipasangkan dengan ``JobEvent``:
#: ``JobEvent`` adalah payload SSE, bukan respons HTTP. Pasangannya adalah
#: ``JobLogEntry``.
RESPONSE_PAIRS: tuple[tuple[str, str], ...] = (
    ("JobResponse", "Job"),
    ("JobListResponse", "JobListResponse"),
    ("SegmentResponse", "Segment"),
    ("RenderResponse", "Render"),
    ("MediaResponse", "MediaInfo"),
    ("JobEventResponse", "JobLogEntry"),
)

#: Pasangan (nama type union TypeScript, model, kolom) — dibandingkan dengan
#: CHECK constraint ``<kolom>_valid`` milik model.
UNION_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("JobStatus", "Job", "status"),
    ("SegmentStatus", "Segment", "status"),
    ("RenderKind", "Render", "kind"),
    ("CropMode", "Render", "crop_mode"),
)

@pytest.fixture(scope="module")
def api_schema() -> dict[str, Any]:
    """Schema OpenAPI yang benar-benar didaftarkan backend."""
    with TestClient(create_app()) as client:
        return client.get("/openapi.json").json()

@pytest.fixture(scope="module")
def types_source() -> str:
    assert TYPES_FILE.exists(), f"tipe frontend tidak ditemukan di {TYPES_FILE}"
    return TYPES_FILE.read_text(encoding="utf-8")

def _strip_comments(source: str) -> str:
    """Buang komentar blok dan baris supaya isinya tidak terbaca sebagai field."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", source)

def _interface_fields(source: str, name: str) -> dict[str, bool]:
    """Field sebuah ``export interface``: ``{nama: opsional?}``.

    Beberapa field boleh berada di satu baris (``id: string; user_id: string;``),
    jadi isi interface dipisah per ``;``, bukan per baris.
    """
    match = re.search(rf"export interface {name}\s*\{{(.*?)\n\}}", _strip_comments(source), re.DOTALL)
    assert match, f"interface {name} tidak ditemukan di types.ts"

    fields: dict[str, bool] = {}
    for entry in match.group(1).split(";"):
        entry = entry.strip()
        if not entry:
            continue
        field = re.match(r"([A-Za-z_][A-Za-z0-9_]*)(\?)?:", entry)
        if field:
            fields[field.group(1)] = field.group(2) == "?"
    return fields

def _union_values(source: str, name: str) -> set[str]:
    """Nilai string sebuah ``export type X = "a" | "b"``."""
    match = re.search(rf"export type {name}\s*=\s*([^;]+);", _strip_comments(source))
    assert match, f"type {name} tidak ditemukan di types.ts"
    return set(re.findall(r'"([^"]+)"', match.group(1)))

def _check_constraint_values(model_name: str, column: str) -> set[str]:
    """Nilai yang diizinkan CHECK constraint ``<kolom>_valid`` milik sebuah model.

    Constraint inilah yang benar-benar menolak nilai salah saat runtime, jadi ia
    yang dipakai sebagai sumber kebenaran — bukan daftar di dokumentasi.
    """
    import app.models  # noqa: F401 — mendaftarkan semua model
    from app.models.base import Base

    model = Base.metadata.tables[{"Job": "jobs", "Segment": "segments", "Render": "renders"}[model_name]]
    suffix = f"_{column}_valid"
    for constraint in model.constraints:
        if isinstance(constraint, CheckConstraint) and (constraint.name or "").endswith(suffix):
            return set(re.findall(r"'([a-z_]+)'", str(constraint.sqltext)))
    raise AssertionError(f"CHECK constraint {model_name}.{column} tidak ditemukan")

class TestResponseFields:
    """Nama dan wajib/opsionalnya field respons harus sama di kedua sisi."""

    @pytest.mark.parametrize(("schema_name", "interface_name"), RESPONSE_PAIRS)
    def test_field_sama_persis(
        self, api_schema: dict[str, Any], types_source: str, schema_name: str, interface_name: str
    ) -> None:
        """Field yang hanya ada di satu sisi adalah bug yang menunggu terjadi."""
        schema = api_schema["components"]["schemas"][schema_name]
        backend = set(schema["properties"])
        frontend = set(_interface_fields(types_source, interface_name))

        assert backend == frontend, (
            f"{schema_name} dan interface {interface_name} tidak cocok. "
            f"Hanya di backend: {sorted(backend - frontend)}. "
            f"Hanya di frontend: {sorted(frontend - backend)}."
        )

    @pytest.mark.parametrize(("schema_name", "interface_name"), RESPONSE_PAIRS)
    def test_opsional_sama_persis(
        self, api_schema: dict[str, Any], types_source: str, schema_name: str, interface_name: str
    ) -> None:
        """``field?`` di TypeScript harus berarti field itu tidak selalu dikirim.

        Backend selalu mengirim setiap field di schema ini (``required`` berisi
        semuanya), jadi ``?`` di sisi frontend berarti frontend menyatakan boleh
        tidak ada padahal selalu ada — atau sebaliknya, frontend mewajibkan field
        yang backend bisa lewatkan. Keduanya membuat pembacaan data menyesatkan.
        """
        schema = api_schema["components"]["schemas"][schema_name]
        required = set(schema.get("required", []))
        frontend = _interface_fields(types_source, interface_name)

        mismatched = {
            field: ("wajib di backend, opsional di TS" if field in required else "opsional di backend, wajib di TS")
            for field, optional in frontend.items()
            if (field in required) == optional
        }
        assert not mismatched, f"{schema_name} vs {interface_name}: {mismatched}"

class TestUnionValues:
    """Nilai union TypeScript harus sama dengan yang diizinkan basis data."""

    @pytest.mark.parametrize(("type_name", "model_name", "column"), UNION_PAIRS)
    def test_union_sama_dengan_check_constraint(
        self, types_source: str, type_name: str, model_name: str, column: str
    ) -> None:
        frontend = _union_values(types_source, type_name)
        backend = _check_constraint_values(model_name, column)

        assert frontend == backend, (
            f"{type_name} tidak cocok dengan CHECK constraint {model_name}.{column}. "
            f"Hanya di TS: {sorted(frontend - backend)}. "
            f"Hanya di basis data: {sorted(backend - frontend)}."
        )

class TestSsePayload:
    """Payload SSE harus memuat tepat field yang dibaca ``JobEvent`` di frontend."""

    def test_kunci_payload_sama_dengan_tipe_job_event(self, types_source: str) -> None:
        """Diambil dari perilaku ``emit``, bukan dari isi berkasnya.

        Bentuk payload adalah kontrak yang tidak terlihat di OpenAPI — frontend
        membacanya dari ``EventSource``. Tanpa test ini, kunci yang berubah nama
        hanya akan terlihat sebagai nilai ``undefined`` di UI.
        """
        from clipper_shared.db import get_db_connection
        from clipper_shared.worker_events import LocalEventBus, emit

        job_id = "aaaaaaaa-1111-2222-3333-444444444444"
        with get_db_connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT OR REPLACE INTO jobs (id, user_id, source_type, status, progress, clip_count,"
                " language, created_at, updated_at) VALUES (%s, %s, 'upload', 'queued', 0, 5, 'id',"
                " '2026-01-01 00:00:00.000000', '2026-01-01 00:00:00.000000')",
                (job_id, "00000000-0000-4000-8000-000000000001"),
            )

        async def capture() -> dict[str, Any]:
            queue = LocalEventBus.register(job_id)
            try:
                await asyncio.to_thread(emit, job_id, "running", "ingest", 5, "pesan")
                return await asyncio.wait_for(queue.get(), timeout=5.0)
            finally:
                LocalEventBus.unregister(job_id, queue)

        event = asyncio.run(capture())
        # ``ts`` hanya ada di payload SSE, bukan di tipe: frontend memakai
        # ``progress``/``stage`` untuk memperbarui tampilan dan tidak membaca
        # waktu kejadian. Kunci itu diperbolehkan ada tanpa pasangan.
        published = set(event) - {"ts"}
        declared = set(_interface_fields(types_source, "JobEvent"))

        assert published == declared, (
            f"payload SSE dan interface JobEvent tidak cocok. "
            f"Hanya di payload: {sorted(published - declared)}. "
            f"Hanya di tipe: {sorted(declared - published)}."
        )
