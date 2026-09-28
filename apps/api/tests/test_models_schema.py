"""Test kesesuaian model ORM dengan TECH_SPEC §3.

Test ini menjaga agar daftar tabel, indeks wajib, dan nilai enum tidak "drift"
dari spesifikasi. Ia murni introspeksi metadata — tidak butuh database.
"""

from __future__ import annotations

from typing import cast

import pytest
from app.models import Base
from app.models.job import Job
from app.models.job_event import JobEvent
from app.models.render import RENDER_KINDS, Render
from app.models.segment import SEGMENT_STATUSES, Segment
from app.models.user import User
from sqlalchemy import Table

#: Sepuluh tabel yang WAJIB ada (TECH_SPEC §3).
EXPECTED_TABLES = {
    "users",
    "jobs",
    "source_media",
    "transcripts",
    "segments",
    "renders",
    "subtitle_presets",
    "social_accounts",
    "scheduled_posts",
    "job_events",
}

#: Tabel tambahan di luar TECH_SPEC §3, beserta alasannya.
#:
#: Dipisahkan dari EXPECTED_TABLES dengan sengaja: daftar ini menandai
#: penyimpangan dari spesifikasi sehingga setiap penambahan harus disengaja dan
#: dapat ditinjau, bukan lolos diam-diam.
#:
#:   ai_provider_settings — menyimpan penyedia AI pilihan pengguna. Kredensial
#:   penyedia milik pengguna sendiri (kuota dan tagihan ada di akun mereka), dan
#:   sebagian pengguna perlu menunjuk endpoint sendiri agar materi internal tidak
#:   dikirim ke penyedia publik. Tidak ada di TECH_SPEC §3 karena fitur ini
#:   ditambahkan setelah spesifikasi ditulis.
#:
#:   font_assets — font kustom yang diunggah pengguna (.ttf/.otf). Container
#:   render tidak punya font pilihan kreator, jadi tanpa tabel ini pilihan font
#:   akan diam-diam jatuh ke font pengganti saat render.
#:
#:   overlay_assets — pustaka B-roll/efek suara milik pengguna (gambar, video,
#:   audio) beserta kata kunci pemicunya.
#:
#:   segment_overlays — penempatan aset overlay pada sebuah segmen, dengan
#:   rentang waktunya. Dipisah dari overlay_assets karena satu aset dapat
#:   dipakai di banyak segmen: menghapus satu penempatan tidak boleh menghapus
#:   berkasnya.
EXTRA_TABLES = {
    "ai_provider_settings",
    "font_assets",
    "overlay_assets",
    "segment_overlays",
}

#: Seluruh tabel yang sah ada di basis data.
ALL_TABLES = EXPECTED_TABLES | EXTRA_TABLES


class TestTables:
    """Semua tabel TECH_SPEC §3 terdaftar di metadata."""

    def test_semua_tabel_ada(self) -> None:
        assert EXPECTED_TABLES.issubset(set(Base.metadata.tables))

    def test_tidak_ada_tabel_tak_terduga(self) -> None:
        """Tabel tak terduga berarti ada model lepas yang perlu ditinjau."""
        extra = set(Base.metadata.tables) - ALL_TABLES
        assert not extra, f"tabel di luar spesifikasi: {sorted(extra)}"

    def test_tabel_tambahan_terdokumentasi(self) -> None:
        """Tabel di luar spesifikasi harus terdaftar eksplisit di EXTRA_TABLES."""
        for table in EXTRA_TABLES:
            assert table in Base.metadata.tables, (
                f"{table} terdaftar sebagai tabel tambahan tetapi modelnya tidak ada"
            )

    def test_import_model_lengkap(self) -> None:
        """Setiap tabel harus punya kelas model yang ter-import (bukan hanya string)."""
        for table in ALL_TABLES:
            assert Base.metadata.tables[table] is not None


class TestRequiredColumns:
    """Nama kolom harus persis seperti di TECH_SPEC §3."""

    @pytest.mark.parametrize(
        ("table", "columns"),
        [
            ("users", {"id", "email", "hashed_password", "plan", "created_at"}),
            (
                "jobs",
                {
                    "id",
                    "user_id",
                    "source_type",
                    "source_url",
                    "status",
                    "stage",
                    "progress",
                    "error",
                    "created_at",
                    "updated_at",
                },
            ),
            (
                "source_media",
                {
                    "id",
                    "job_id",
                    "r2_key",
                    "size_bytes",
                    "duration_s",
                    "codec",
                    "width",
                    "height",
                    "upload_id",
                    "expires_at",
                },
            ),
            (
                "transcripts",
                {"id", "job_id", "language", "words", "speakers", "full_text", "model_used"},
            ),
            (
                "segments",
                {
                    "id",
                    "job_id",
                    "start_s",
                    "end_s",
                    "score",
                    "label",
                    "hook_score",
                    "completeness",
                    "emotional_arc",
                    "reason",
                    "status",
                },
            ),
            (
                "renders",
                {
                    "id",
                    "segment_id",
                    "kind",
                    "r2_key",
                    "preset",
                    "subtitle_style",
                    "status",
                    "duration_ms",
                    "size_bytes",
                },
            ),
            ("subtitle_presets", {"id", "user_id", "name", "style"}),
            (
                "social_accounts",
                {
                    "id",
                    "user_id",
                    "platform",
                    "encrypted_token",
                    "refresh_token",
                    "expires_at",
                    "scopes",
                },
            ),
            (
                "scheduled_posts",
                {
                    "id",
                    "user_id",
                    "render_id",
                    "platform",
                    "caption",
                    "hashtags",
                    "scheduled_at",
                    "status",
                },
            ),
            ("job_events", {"id", "job_id", "stage", "message", "payload", "created_at"}),
        ],
    )
    def test_kolom_wajib_ada(self, table: str, columns: set[str]) -> None:
        actual = {column.name for column in Base.metadata.tables[table].columns}
        missing = columns - actual
        assert not missing, f"kolom hilang di {table}: {sorted(missing)}"


class TestRequiredIndexes:
    """Indeks wajib TECH_SPEC §3 harus benar-benar terpasang."""

    def test_jobs_index_user_created_desc(self) -> None:
        from sqlalchemy import Index

        names = {
            index.name for index in Base.metadata.tables["jobs"].indexes if isinstance(index, Index)
        }
        assert "ix_jobs_user_id_created_at_desc" in names

    def test_segments_index_job_score_desc(self) -> None:
        names = {index.name for index in Base.metadata.tables["segments"].indexes}
        assert "ix_segments_job_id_score_desc" in names

    def test_job_events_index_job_created(self) -> None:
        names = {index.name for index in Base.metadata.tables["job_events"].indexes}
        assert "ix_job_events_job_id_created_at" in names


class TestEnums:
    """Nilai enum harus persis seperti TECH_SPEC §3."""

    def test_segment_statuses(self) -> None:
        assert SEGMENT_STATUSES == ("proposed", "selected", "rejected")

    def test_render_kinds(self) -> None:
        assert RENDER_KINDS == ("preview", "final")

    def test_source_type_terbatas_upload_youtube(self) -> None:
        table = cast(Table, Job.__table__)
        constraints = {constraint.name for constraint in table.constraints}
        assert "ck_jobs_source_type_valid" in constraints

    def test_segment_default_status_proposed(self) -> None:
        """Default harus 'proposed', bukan 'pending' — frontend memakai istilah spek."""
        column = Segment.__table__.columns["status"]
        assert column.default is not None
        assert column.default.arg == "proposed"

    def test_renders_kind_check_constraint(self) -> None:
        table = cast(Table, Render.__table__)
        constraints = {constraint.name for constraint in table.constraints}
        assert "ck_renders_kind_valid" in constraints

    def test_job_events_append_only_shape(self) -> None:
        """job_events hanya butuh created_at (tidak ada updated_at)."""
        columns = {column.name for column in JobEvent.__table__.columns}
        assert "created_at" in columns
        assert "updated_at" not in columns

    def test_users_email_unik(self) -> None:
        assert User.__table__.columns["email"].unique is True
