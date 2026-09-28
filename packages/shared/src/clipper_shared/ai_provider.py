"""Penyedia AI kustom dan pemilihan model untuk skoring viral.

Permintaan pengguna: "Custom AI Provider untuk saran hasil". Maksudnya,
pengguna dapat menunjuk endpoint LLM miliknya sendiri (OpenAI, OpenRouter,
Groq, Together, Ollama lokal, dsb.) alih-alih terpaku pada satu vendor.

**Mengapa ini penting secara arsitektur, bukan sekadar fitur tambahan:**

1. Kami tidak dapat menjamin ketersediaan satu vendor. Bila Gemini sedang
   gangguan, pengguna tetap bisa bekerja dengan endpoint pilihannya.
2. Pengguna dengan GPU sendiri dapat menjalankan model lokal, dan itu
   menghilangkan biaya per-panggilan sekaligus menjaga transkrip tetap di
   infrastrukturnya sendiri — penting untuk materi yang sensitif.
3. Biaya per tontonan berbeda drastis antar-penyedia; membiarkan pengguna
   memilih adalah cara paling jujur mengendalikan biaya.

**Bentuk yang dipakai: OpenAI-compatible.** Hampir semua penyedia LLM modern
menyediakan endpoint ``/chat/completions`` yang kompatibel dengan OpenAI. Satu
implementasi klien melayani semuanya.

**Peringatan keamanan:** ``base_url`` yang ditentukan pengguna adalah vektor
SSRF — pengguna dapat mengarahkannya ke alamat internal (mis.
``http://169.254.169.254/`` untuk metadata cloud, atau ``http://postgres:5432``).
Karena itu :func:`validate_base_url` menolak alamat privat/loopback kecuali
mode pengembangan mengizinkannya secara eksplisit.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypedDict
from urllib.parse import urlparse

#: Kredensial tidak dikirim bila endpoint menunjuk ke host lokal (Ollama/LM Studio).
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "host.docker.internal"})


class ProviderPreset(StrEnum):
    """Preset penyedia yang dikenal. Menentukan URL dasar dan model bawaan."""

    GEMINI = "gemini"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OPENROUTER = "openrouter"
    GROQ = "groq"
    TOGETHER = "together"
    OLLAMA = "ollama"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class ProviderDefaults:
    """Nilai bawaan satu penyedia."""

    label: str
    base_url: str
    default_model: str
    requires_api_key: bool = True
    #: True bila penyedia menyediakan endpoint bergaya OpenAI di base_url ini.
    openai_compatible: bool = True


#: Peta preset. URL ditulis tanpa ``/chat/completions`` — klien menambahkannya,
#: supaya pengguna yang menempel URL lengkap tidak menghasilkan path ganda.
PROVIDER_PRESETS: dict[ProviderPreset, ProviderDefaults] = {
    ProviderPreset.GEMINI: ProviderDefaults(
        label="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.5-flash",
    ),
    ProviderPreset.OPENAI: ProviderDefaults(
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
    ),
    ProviderPreset.ANTHROPIC: ProviderDefaults(
        label="Anthropic Claude",
        # Anthropic menyediakan lapisan kompatibilitas OpenAI.
        base_url="https://api.anthropic.com/v1",
        default_model="claude-haiku-4-5",
    ),
    ProviderPreset.OPENROUTER: ProviderDefaults(
        label="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        default_model="google/gemini-2.5-flash",
    ),
    ProviderPreset.GROQ: ProviderDefaults(
        label="Groq",
        base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
    ),
    ProviderPreset.TOGETHER: ProviderDefaults(
        label="Together AI",
        base_url="https://api.together.xyz/v1",
        default_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
    ),
    ProviderPreset.OLLAMA: ProviderDefaults(
        label="Ollama (lokal)",
        base_url="http://localhost:11434/v1",
        default_model="llama3.1:8b",
        requires_api_key=False,
    ),
    ProviderPreset.CUSTOM: ProviderDefaults(
        label="Bring Your API Key (Custom)",
        base_url="",
        default_model="",
    ),
}


class ProviderConfig(TypedDict):
    """Konfigurasi penyedia AI untuk sebuah job, dibaca dari basis data/env.

    Mengapa TypedDict: konfigurasi ini berisi CAMPURAN tipe (string dan bool),
    sehingga ``dict[str, str]`` secara teknis berbohong — ``allow_private_host``
    adalah bool. Anotasi ``dict[str, str]`` sebelumnya menyebabkan mypy
    menandai nilai bool, sedangkan ``dict[str, object]`` justru menghapus tipe
    yang dipakai pemanggil (``resolve_provider`` butuh ``str`` untuk preset).
    TypedDict memberi tipe tepat per-kolom tanpa mengubah representasi runtime.
    """

    preset: str
    base_url: str
    model: str
    api_key: str
    allow_private_host: bool
    default_direction: str
    #: Jendela konteks model (token). ``None`` = tidak diketahui → cadangan.
    context_tokens: int | None


#: Konteks yang diasumsikan bila penyedia tidak melaporkan ``context_length``
#: dan pengguna tidak mengisinya (mis. OpenAI/Ollama ``/models``).
DEFAULT_CONTEXT_TOKENS = 128_000
#: Token di luar transkrip: instruksi sistem (~250, terukur), arahan pengguna
#: (maks 2000 karakter ≈ 700), dan jawaban JSON hingga 33 segmen (30 klip + 3
#: cadangan, ~130 token/segmen ≈ 4.300).
RESERVED_PROMPT_TOKENS = 6_144
#: Token transkrip per menit video. Terukur pada format prompt
#: ``_render_transcript`` (podcast id/en, 79–316 menit): 217–268 token/menit.
#: Dibulatkan ke atas ~1,5× untuk pembicara cepat.
TRANSCRIPT_TOKENS_PER_MINUTE = 400
#: Karakter per token. Terukur 3,61–3,81; diambil lebih rendah agar batas
#: karakter tidak pernah melampaui konteks sungguhan.
TRANSCRIPT_CHARS_PER_TOKEN = 3
#: Batas atas durasi terlepas dari model: transkripsi CPU ~1× realtime dan
#: video mentah 1080p ~2 GB/jam. Dapat ditimpa ``MAX_VIDEO_DURATION_MIN``.
DEFAULT_MAX_VIDEO_MINUTES = 180


def transcript_token_budget(context_tokens: int | None) -> int:
    """Token yang tersedia untuk transkrip di jendela konteks model."""
    return max(0, (context_tokens or DEFAULT_CONTEXT_TOKENS) - RESERVED_PROMPT_TOKENS)


def transcript_char_budget(context_tokens: int | None) -> int:
    """Batas panjang transkrip (karakter, format prompt) untuk model ini."""
    return transcript_token_budget(context_tokens) * TRANSCRIPT_CHARS_PER_TOKEN


def max_video_minutes(context_tokens: int | None) -> int:
    """Durasi video maksimum: kapasitas konteks model, dibatasi batas atas env."""
    ceiling = int(os.getenv("MAX_VIDEO_DURATION_MIN", str(DEFAULT_MAX_VIDEO_MINUTES)))
    return min(ceiling, transcript_token_budget(context_tokens) // TRANSCRIPT_TOKENS_PER_MINUTE)


def env_provider_config() -> ProviderConfig:
    """Konfigurasi cadangan dari env, dipakai bila pengguna belum menyimpan apa pun di UI.

    Satu sumber untuk worker (saat skoring) dan API (cek sebelum job dibuat),
    supaya keduanya tidak bisa berbeda pendapat tentang "penyedia sudah siap".
    """
    preset = os.getenv("AI_PROVIDER_PRESET", ProviderPreset.CUSTOM.value)
    # Nilai tidak dikenal (salah ketik di .env) jatuh ke "custom" — melempar
    # ValueError di sini akan menggagalkan cek kesiapan dan setiap skoring.
    if preset not in {p.value for p in ProviderPreset}:
        preset = ProviderPreset.CUSTOM.value
    defaults = PROVIDER_PRESETS.get(ProviderPreset(preset))
    return {
        "preset": preset,
        "base_url": os.getenv("CUSTOM_AI_BASE_URL", "") or (defaults.base_url if defaults else ""),
        "model": os.getenv("CUSTOM_AI_MODEL", "") or (defaults.default_model if defaults else ""),
        "api_key": os.getenv("CUSTOM_AI_API_KEY", "") or os.getenv("GEMINI_API_KEY", ""),
        "allow_private_host": os.getenv("ALLOW_PRIVATE_AI_HOST", "false").lower() == "true",
        "default_direction": "",
        "context_tokens": int(os.getenv("AI_CONTEXT_TOKENS", "0")) or None,
    }


def effective_allow_private(config: ProviderConfig) -> bool:
    """Izin host privat: toggle tersimpan, atau ``ALLOW_PRIVATE_AI_HOST`` sebagai cadangan."""
    return bool(config.get("allow_private_host")) or (
        os.getenv("ALLOW_PRIVATE_AI_HOST", "false").lower() == "true"
    )


def provider_config_problem(config: ProviderConfig) -> str | None:
    """Alasan konfigurasi tidak dapat dipakai skoring, atau ``None`` bila siap.

    Memakai :func:`resolve_provider` yang sama dengan worker, jadi cek di API
    tidak bisa lebih longgar atau lebih ketat daripada saat skoring berjalan.
    """
    try:
        resolve_provider(
            preset=config["preset"],
            base_url=config["base_url"],
            model=config["model"],
            api_key=config["api_key"],
            allow_private=effective_allow_private(config),
        )
    except ValueError as exc:
        return str(exc)
    if max_video_minutes(config.get("context_tokens")) < 1:
        return (
            f"Konteks model ({config.get('context_tokens') or DEFAULT_CONTEXT_TOKENS} token) "
            "terlalu kecil untuk transkrip video. Pilih model berkonteks lebih besar."
        )
    return None


@dataclass(frozen=True, slots=True)
class AIProviderConfig:
    """Konfigurasi penyedia yang sudah tervalidasi dan siap dipakai."""

    preset: ProviderPreset
    base_url: str
    model: str
    api_key: str = ""
    #: Timeout per panggilan. Transkrip panjang butuh waktu lebih lama.
    timeout_s: int = 180
    max_retries: int = 4
    extra_headers: dict[str, str] = field(default_factory=dict)

    @property
    def chat_completions_url(self) -> str:
        """URL akhir untuk chat completions, tanpa duplikasi path."""
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"


#: Nama host layanan internal yang lazim di Docker Compose / Kubernetes.
#: Ini penting: pemeriksaan berbasis IP saja TIDAK menangkapnya, karena
#: "postgres" atau "redis" bukan alamat IP — resolusi DNS-nya terjadi di dalam
#: jaringan kontainer. Tanpa daftar ini, pengguna dapat memakai fitur "penyedia
#: kustom" untuk menjangkau database kami sendiri (SSRF).
INTERNAL_HOSTNAMES = frozenset(
    {
        "postgres",
        "postgresql",
        "redis",
        "minio",
        "api",
        "worker-light",
        "worker-render",
        "db",
        "database",
        "cache",
        "metadata",
        "metadata.google.internal",
        "kubernetes",
        "kubernetes.default",
        "host.docker.internal",
        "gateway.docker.internal",
    }
)


def _is_private_host(hostname: str) -> bool:
    """True bila host menunjuk ke jaringan privat/loopback/link-local/internal.

    Menangani tiga bentuk: nama loopback yang dikenal, alamat IP privat, dan
    nama layanan internal yang hanya bermakna di dalam jaringan kontainer.
    """
    lowered = hostname.lower().rstrip(".")

    if lowered in LOCAL_HOSTS or lowered in INTERNAL_HOSTNAMES:
        return True

    # Nama tanpa titik (mis. "postgres") hanya bisa diresolusi lewat DNS internal.
    # Domain publik selalu punya titik, jadi ini menangkap nama layanan yang
    # belum tercantum di daftar tanpa menolak domain sah.
    if "." not in lowered and lowered != "localhost":
        return True

    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        # Bukan IP; biarkan lolos — pemeriksaan DNS-rebinding berada di luar
        # cakupan modul ini dan harus ditangani di lapisan jaringan.
        return False
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    )


def validate_base_url(base_url: str, *, allow_private: bool = False) -> str:
    """Validasi dan normalkan ``base_url`` penyedia.

    Menolak skema selain http/https dan menolak host privat/link-local —
    mencegah pengguna memakai endpoint AI sebagai alat memindai jaringan
    internal (SSRF).

    Args:
        allow_private: Setel True hanya di mode pengembangan, atau bila
            pengguna memang menjalankan model lokal (Ollama).

    Raises:
        ValueError: dengan pesan yang bisa ditampilkan langsung ke pengguna.
    """
    candidate = (base_url or "").strip()
    if not candidate:
        raise ValueError("URL penyedia AI tidak boleh kosong.")

    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("URL penyedia harus diawali http:// atau https://")

    if not parsed.hostname:
        raise ValueError("URL penyedia tidak memuat nama host yang sah.")

    if not allow_private and _is_private_host(parsed.hostname):
        raise ValueError(
            "URL penyedia menunjuk ke alamat privat atau lokal. "
            "Aktifkan 'izinkan penyedia lokal' bila Anda memang menjalankan "
            "model di jaringan sendiri."
        )

    return candidate.rstrip("/")


def resolve_provider(
    *,
    preset: ProviderPreset | str = ProviderPreset.GEMINI,
    base_url: str | None = None,
    model: str | None = None,
    api_key: str = "",
    allow_private: bool = False,
) -> AIProviderConfig:
    """Susun konfigurasi penyedia dari preset + penimpaan (override).

    Pengguna dapat memilih preset lalu menimpanya sebagian. Contoh: preset
    "Groq" tetapi model diganti.

    Raises:
        ValueError: bila penyedia mewajibkan API key tetapi tidak diberikan,
            atau URL tidak lolos validasi SSRF.
    """
    key = ProviderPreset(preset) if not isinstance(preset, ProviderPreset) else preset
    defaults = PROVIDER_PRESETS[key]

    resolved_url = base_url.strip().rstrip("/") if base_url and base_url.strip() else defaults.base_url
    if not resolved_url:
        raise ValueError("Penyedia 'custom' memerlukan URL dasar yang diisi.")

    resolved_url = validate_base_url(resolved_url, allow_private=allow_private)

    resolved_model = (model or "").strip() or defaults.default_model
    if not resolved_model:
        raise ValueError("Penyedia 'custom' memerlukan nama model yang diisi.")

    cleaned_key = api_key.strip()
    if defaults.requires_api_key and not cleaned_key:
        raise ValueError(f"Penyedia {defaults.label} memerlukan API key.")

    return AIProviderConfig(
        preset=key,
        base_url=resolved_url,
        model=resolved_model,
        api_key=cleaned_key,
    )


def describe_providers() -> list[dict[str, object]]:
    """Daftar penyedia untuk mengisi dropdown UI."""
    return [
        {
            "id": preset.value,
            "label": defaults.label,
            "default_model": defaults.default_model,
            "requires_api_key": defaults.requires_api_key,
            "base_url": defaults.base_url,
        }
        for preset, defaults in PROVIDER_PRESETS.items()
    ]
