"""Klasifikasi suasana klip dari penanda reaksi di transkrip.

**Kenapa deterministik, bukan LLM.** Penanda seperti ``[tertawa]`` dan
``[berteriak]`` sudah ada di transkrip YouTube — menghitungnya gratis, bisa
diulang, dan tidak bergantung pada mood model hari itu. LLM baru dipakai untuk
kategori yang *tidak* punya penanda (sedih, menegangkan); lihat
:func:`classify_mood` untuk batasannya.

**Batasan yang jujur.** Hanya empat kategori yang punya bukti tekstual:

* ``komedi``   — ``[tertawa]`` dominan;
* ``musik``    — ``[musik]`` / ``[bernyanyi]``;
* ``reaksi``   — ``[berteriak]`` / ``[mendengus]``;
* ``netral``   — tidak ada penanda.

Kategori seperti *sedih*, *menegangkan*, atau *serius* **tidak bisa**
ditentukan dari penanda ini. Klip cerita pengejaran yang menegangkan sering
tidak punya penanda sama sekali, jadi ia akan jatuh ke ``netral``. Itu
kekurangan yang nyata, bukan yang dibesar-besarkan.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "MOOD_CATEGORIES",
    "Mood",
    "MoodEvidence",
    "classify_mood",
    "count_markers",
    "extract_markers",
]

#: Kategori yang didukung. Sengaja sedikit: hanya yang punya bukti tekstual.
MOOD_CATEGORIES: tuple[str, ...] = ("komedi", "musik", "reaksi", "netral")

#: Kategori untuk klasifikasi berbasis LLM (opsi B). Terpisah dari
#: :data:`MOOD_CATEGORIES` karena dua hal: penanda reaksi tidak bisa
#: mendeteksi *sedih* atau *menegangkan*, dan hasil LLM tidak bisa diulang
#: persis — jadi keduanya tidak boleh tertukar tanpa jejak.
LLM_MOOD_CATEGORIES: tuple[str, ...] = (
    "komedi",
    "emosional",
    "menegangkan",
    "informatif",
    "hangat",
    "netral",
)

#: Nilai jatuh-tempo bila LLM gagal atau membalas di luar kategori.
LLM_MOOD_FALLBACK = "netral"

#: Penanda yang dikenali, dikelompokkan per kategori.
_MARKER_GROUPS: dict[str, tuple[str, ...]] = {
    "komedi": ("tertawa", "tertawa keras", "terbahak-bahak"),
    "musik": ("musik", "bernyanyi", "lagu"),
    "reaksi": ("berteriak", "mendengus", "terkejut", "bersorak"),
}

#: Pola penanda reaksi: ``[tertawa]``, ``[Musik]``, dst. Huruf awal tidak
#: dibedakan karena caption otomatis tidak konsisten.
_MARKER_RE = re.compile(r"\[([^\[\]]+)\]", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Mood:
    """Hasil klasifikasi satu segmen.

    Attributes:
        category: Salah satu dari :data:`MOOD_CATEGORIES`.
        counts: Jumlah penanda per kategori, untuk jejak dan pengujian.
    """

    category: str
    counts: dict[str, int]

    @property
    def is_confident(self) -> bool:
        """Apakah klasifikasinya punya bukti, bukan sekadar tidak ada penanda.

        ``netral`` berarti "tidak ada penanda yang dikenali" — itu berbeda
        dengan "pasti tidak ada emosi". Properti ini membedakan keduanya
        supaya pemanggil tidak membaca ``netral`` sebagai klaim kuat.
        """
        return self.category != "netral"


@dataclass(frozen=True, slots=True)
class MoodEvidence:
    """Bukti mentah di balik :class:`Mood`.

    Attributes:
        markers: Semua penanda yang ditemukan, urut kemunculan.
        counts: Jumlah per kategori.
    """

    markers: tuple[str, ...]
    counts: dict[str, int]


def extract_markers(transcript: str) -> tuple[str, ...]:
    """Ambil semua penanda reaksi dari transkrip.

    Args:
        transcript: Teks transkrip, boleh berisi ``[tertawa]`` dan sebagainya.

    Returns:
        Penanda yang ditemukan, urut kemunculan, apa adanya.
    """
    if not transcript:
        return ()
    return tuple(match.group(1).strip() for match in _MARKER_RE.finditer(transcript))


def count_markers(transcript: str) -> dict[str, int]:
    """Hitung penanda per kategori suasana.

    Args:
        transcript: Teks transkrip.

    Returns:
        Kamus ``{kategori: jumlah}``. Kategori tanpa penanda ikut bernilai 0
        supaya pemanggil tidak perlu menebak apakah kuncinya ada.
    """
    counts = {category: 0 for category in MOOD_CATEGORIES if category != "netral"}
    for marker in extract_markers(transcript):
        lowered = marker.lower()
        for category, names in _MARKER_GROUPS.items():
            if lowered in names:
                counts[category] += 1
                break
    return counts


def classify_mood(transcript: str) -> Mood:
    """Klasifikasi suasana satu segmen dari penanda reaksi.

    Kategori dengan penanda terbanyak menang. Bila seri, urutan prioritas
    mengikuti :data:`MOOD_CATEGORIES` — ``komedi`` diutamakan karena
    ``[tertawa]`` adalah penanda paling dapat diandalkan.

    Args:
        transcript: Teks transkrip segmen.

    Returns:
        :class:`Mood`. ``netral`` bila tidak ada penanda yang dikenali.

    Examples:
        >>> classify_mood("haha [tertawa] lagi [tertawa]").category
        'komedi'
        >>> classify_mood("cerita panjang tanpa penanda").category
        'netral'
    """
    counts = count_markers(transcript)
    if not any(counts.values()):
        return Mood(category="netral", counts=counts)

    best = max(MOOD_CATEGORIES[:-1], key=lambda category: counts.get(category, 0))
    return Mood(category=best, counts=counts)


class ChatPost(Protocol):
    """Kontrak fungsi pengirim permintaan chat.

    Disuntikkan supaya :func:`classify_mood_llm` bisa diuji tanpa jaringan.
    """

    def __call__(
        self,
        config: Any,
        messages: Sequence[dict[str, str]],
        *,
        timeout_s: float,
    ) -> str: ...


def classify_mood_llm(
    transcript: str,
    *,
    post: ChatPost,
    config: Any,
    categories: tuple[str, ...] = LLM_MOOD_CATEGORIES,
    timeout_s: float = 60.0,
) -> str:
    """Klasifikasi suasana berbasis LLM untuk kategori tanpa penanda.

    Hanya untuk ``emosional``, ``menegangkan``, ``informatif``, ``hangat``.
    Kategori ``komedi``/``musik``/``reaksi`` sebaiknya memakai
    :func:`classify_mood` yang deterministik — gratis dan bisa diulang.

    Args:
        transcript: Teks transkrip segmen.
        post: Fungsi ``(config, messages, timeout_s) -> str`` yang mengirim
            permintaan dan mengembalikan isi balasan. Disuntikkan supaya
            modul ini tetap bisa diuji tanpa jaringan.
        config: Konfigurasi penyedia yang sudah di-resolve.
        categories: Kategori yang ditawarkan.
        timeout_s: Batas waktu, detik.

    Returns:
        Kategori yang dipilih, atau :data:`LLM_MOOD_FALLBACK` bila gagal
        atau balasannya di luar kategori.
    """
    from clipper_shared.scoring_prompt import build_mood_prompt

    if not transcript.strip():
        return LLM_MOOD_FALLBACK

    content = post(
        config,
        build_mood_prompt(transcript=transcript, categories=categories),
        timeout_s=timeout_s,
    )
    if not content:
        return LLM_MOOD_FALLBACK

    # Model kadang menambahkan tanda baca atau spasi; rapikan sebelum
    # mencocokkan. Bila balasannya lebih dari satu kata, anggap gagal —
    # kontrak meminta tepat satu kata.
    answer: str = content.strip().strip(" .,!?\n\t").lower()
    if answer in categories:
        return answer
    return LLM_MOOD_FALLBACK


def classify_with_evidence(transcript: str) -> MoodEvidence:
    """Seperti :func:`classify_mood`, tetapi menyertakan bukti mentahnya.

    Args:
        transcript: Teks transkrip segmen.

    Returns:
        :class:`MoodEvidence` berisi semua penanda dan hitungannya.
    """
    return MoodEvidence(markers=extract_markers(transcript), counts=count_markers(transcript))
