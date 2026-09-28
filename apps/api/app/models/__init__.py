"""Registry semua model ORM ClipperAI.

Alembic autogenerate dan ``Base.metadata.create_all`` bergantung pada modul ini:
kalau sebuah model tidak di-import di sini, tabelnya tidak akan terdeteksi.

Sepuluh tabel inti dari TECH_SPEC §3: users, jobs, source_media, transcripts,
segments, renders, subtitle_presets, social_accounts, scheduled_posts,
job_events.

Tiga tabel tambahan menyusul setelah spec ditulis, karena kebutuhan fitur yang
nyata: ``ai_provider_settings`` (kredensial penyedia AI per pengguna),
``font_assets`` (font kustom yang diunggah pengguna), dan ``app_settings``
bila kelak diperlukan. Tabel tambahan TIDAK dimasukkan ke
:data:`TECH_SPEC_TABLES` supaya test kesesuaian spec tetap bermakna.
"""

from __future__ import annotations

from app.models.ai_provider_settings import AiProviderSettings
from app.models.base import Base
from app.models.font_asset import FontAsset
from app.models.job import Job
from app.models.job_event import JobEvent
from app.models.overlay import OverlayAsset, SegmentOverlay
from app.models.render import Render
from app.models.scheduled_post import ScheduledPost
from app.models.segment import Segment
from app.models.social_account import SocialAccount
from app.models.source_media import SourceMedia
from app.models.subtitle_preset import SubtitlePreset
from app.models.transcript import Transcript
from app.models.user import User

__all__ = [
    "AiProviderSettings",
    "Base",
    "FontAsset",
    "Job",
    "JobEvent",
    "OverlayAsset",
    "Render",
    "ScheduledPost",
    "Segment",
    "SegmentOverlay",
    "SocialAccount",
    "SourceMedia",
    "SubtitlePreset",
    "Transcript",
    "User",
]

#: Daftar nama tabel yang WAJIB ada — dipakai test untuk menjaga kesesuaian
#: dengan TECH_SPEC §3.
TECH_SPEC_TABLES: tuple[str, ...] = (
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
)
