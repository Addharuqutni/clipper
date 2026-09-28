"""ClipperAI shared contracts.

Paket ini di-import oleh API, worker-light, DAN worker-render. Karena itu ia
harus tetap bebas dari dependensi berat (torch, mediapipe, fastapi). Lihat
TECH_SPEC §1 (isolasi container A/B/C).

Catatan: ``reframe`` dan ``subtitles`` sengaja hanya memuat matematika dan
strategy — tanpa OpenCV/MediaPipe — sehingga bisa diuji di container yang tidak
memuat dependensi berat, dan worker-render cukup mengirim koordinat/durasi.
"""

from __future__ import annotations

from clipper_shared.ai_provider import (
    PROVIDER_PRESETS,
    AIProviderConfig,
    ProviderPreset,
    describe_providers,
    resolve_provider,
    validate_base_url,
)
from clipper_shared.reframe import (
    DEFAULT_CROP_MODE,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    CropGeometry,
    CropMode,
    CropSmoother,
    build_filter_chain,
    describe_mode,
    plan_letterbox,
)
from clipper_shared.stt import (
    FasterWhisperLocal,
    RemoteWhisperAPI,
    Transcriber,
    TranscriptResult,
    TranscriptWord,
    get_transcriber,
)
from clipper_shared.subtitles import (
    SubtitleStyle,
    SubtitleTrack,
    SubtitleWord,
    parse_subtitle,
    pick_track,
    render_ass,
)
from clipper_shared.workspace import make_workspace, workspace_root
from clipper_shared.youtube_cookies import (
    CookieValidation,
    validate_cookie_content,
    validate_cookie_file,
)

__all__ = [
    "DEFAULT_CROP_MODE",
    "OUTPUT_HEIGHT",
    "OUTPUT_WIDTH",
    "PROVIDER_PRESETS",
    "AIProviderConfig",
    "CookieValidation",
    "CropGeometry",
    "CropMode",
    "CropSmoother",
    "FasterWhisperLocal",
    "ProviderPreset",
    "RemoteWhisperAPI",
    "SubtitleStyle",
    "SubtitleTrack",
    "SubtitleWord",
    "TranscriptResult",
    "TranscriptWord",
    "Transcriber",
    "build_filter_chain",
    "describe_mode",
    "describe_providers",
    "get_transcriber",
    "make_workspace",
    "parse_subtitle",
    "pick_track",
    "plan_letterbox",
    "render_ass",
    "resolve_provider",
    "validate_base_url",
    "validate_cookie_content",
    "validate_cookie_file",
    "workspace_root",
]
