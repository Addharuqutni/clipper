"""Klien skoring LLM dengan pengerasan output (TECH_SPEC §5.4).

Modul ini menerapkan pelajaran dari repo referensi tentang cara **tidak**
mempercayai LLM:

1. **Minta berlebih lalu saring.** Meminta "tepat 5 segmen" tidak dapat
   diandalkan; meminta 8 lalu menyaring 5 hampir selalu berhasil.
2. **Selamatkan, jangan gagal.** Satu tanda kutip tak ter-escape merusak seluruh
   array JSON. Parser di sini menelusuri dan mengambil objek yang valid.
3. **Temperature menurut niat.** 1.0 tanpa arahan (variasi diinginkan),
   0.3 dengan arahan (kepatuhan lebih penting daripada kreativitas).
4. **Pertahanan prompt injection.** Teks arahan pengguna adalah masukan bebas.
5. **Klasifikasi kesalahan.** Masalah penyedia dan masalah masukan memerlukan
   tindakan berbeda, jadi pesannya harus berbeda.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from clipper_shared.ai_provider import (
    AIProviderConfig,
    ProviderConfig,
    resolve_provider,
    transcript_char_budget,
)
from clipper_shared.mood import ChatPost
from clipper_shared.scoring import MAX_LABEL_CHARS
from clipper_shared.scoring_prompt import (
    SCORING_PROMPT_VERSION,
    build_scoring_prompt,
)

__all__ = ["MAX_LABEL_CHARS", "SCORING_PROMPT_VERSION"]

logger = logging.getLogger(__name__)

#: Batas panjang arahan pengguna yang diteruskan ke model.
MAX_USER_DIRECTION_CHARS = 2000

#: Rentang durasi segmen (PRD FR-2.2).
MIN_SEGMENT_S = 25.0
MAX_SEGMENT_S = 65.0

# MAX_LABEL_CHARS diimpor dari clipper_shared.scoring — lihat impor di atas.

#: Toleransi pencocokan rentang waktu yang diminta pengguna.
RANGE_MATCH_TOLERANCE_S = 8.0

#: Percobaan pemanggilan LLM untuk kegagalan sementara (timeout, 429, 5xx).
MAX_ATTEMPTS = 4
RETRY_BASE_DELAY_S = 2.0

#: Placeholder milik template prompt. Dibersihkan dari masukan pengguna agar
#: tidak bisa menyuntikkan transkrip kedua atau mengosongkan variabel.
_PLACEHOLDER_RE = re.compile(
    r"\{(?:num_clips|video_context|transcript|user_direction|output_language)\}"
)

#: Pagar pembatas blok arahan pengguna.
_DELIMITER_RE = re.compile(r"---\s*USER DIRECTION (?:START|END)\s*---", re.IGNORECASE)

#: Rentang jam eksplisit: "2:00 - 2:50", "dari 21:30 sampai 22:25".
_TIME_RANGE_RE = re.compile(
    r"(\d{1,2}:\d{2}(?::\d{2})?)"
    r"\s*(?:-|–|—|s/d|sd|sampai|hingga|ke|to|until)\s*"
    r"(\d{1,2}:\d{2}(?::\d{2})?)",
    re.IGNORECASE,
)


@dataclass
class ScoredSegment:
    """Satu kandidat segmen hasil penilaian.

    Attributes:
        mood: Klasifikasi suasana (``komedi``/``musik``/``reaksi``/``netral``)
            dari penanda reaksi di transkrip. ``netral`` berarti tidak ada
            penanda yang dikenali — bukan klaim bahwa segmennya tidak emosional.
    """

    start_s: float
    end_s: float
    score: float
    label: str
    hook_score: float
    completeness: float
    emotional_arc: float
    reason: str
    mood: str = "netral"


@dataclass
class ScoringOutcome:
    """Hasil satu pemanggilan skoring."""

    segments: list[ScoredSegment] = field(default_factory=list)
    #: Berapa objek yang dilewati karena JSON rusak sebagian.
    recovered_count: int = 0
    provider_label: str = ""
    error: str = ""
    raw_length: int = 0


def _post_chat(
    config: AIProviderConfig,
    messages: list[dict[str, str]],
    *,
    timeout_s: float,
) -> str:
    """Kirim satu permintaan chat dan kembalikan isinya.

    Dipakai untuk tugas kecil (klasifikasi suasana) yang tidak butuh logika
    retry milik ``score_job``. Kegagalan dikembalikan sebagai string kosong —
    pemanggil menentukan nilai jatuh-tempo.

    Args:
        config: Konfigurasi penyedia yang sudah di-resolve.
        messages: Pesan gaya OpenAI.
        timeout_s: Batas waktu, detik.

    Returns:
        Isi balasan, atau string kosong bila gagal.
    """
    headers = {"content-type": "application/json"}
    if config.api_key:
        headers["authorization"] = f"Bearer {config.api_key}"
    body = {
        "model": config.model,
        "messages": messages,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    try:
        with httpx.Client(timeout=timeout_s) as client:
            response = client.post(config.chat_completions_url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        logger.warning("Panggilan chat gagal: %s", exc)
        return ""
    if response.status_code >= 400:
        logger.warning("Penyedia membalas HTTP %s.", response.status_code)
        return ""

    return _extract_content(response.text)


def _mood_for(
    start_s: float,
    end_s: float,
    words: list[dict[str, Any]] | None,
    *,
    llm_post: ChatPost | None = None,
    llm_config: AIProviderConfig | None = None,
) -> str:
    """Klasifikasi suasana segmen dari penanda reaksi di transkrip.

    Deterministik (``clipper_shared.mood``), bukan LLM: penanda seperti
    ``[tertawa]`` sudah ada di transkrip YouTube, jadi menghitungnya gratis
    dan bisa diulang. ``netral`` berarti tidak ada penanda yang dikenali —
    bukan klaim bahwa segmennya tidak emosional.

    Args:
        start_s: Awal segmen.
        end_s: Akhir segmen.
        words: Seluruh kata transkrip job.

    Returns:
        Salah satu ``komedi``/``musik``/``reaksi``/``netral``.
    """
    from clipper_shared.mood import LLM_MOOD_CATEGORIES, classify_mood, classify_mood_llm

    if not words:
        return "netral"

    text = " ".join(
        str(word.get("text") or "")
        for word in words
        if start_s <= float(word.get("start_s") or 0.0) < end_s
    )

    # Opsi A dulu: penanda reaksi sudah ada di transkrip, jadi gratis dan
    # bisa diulang. Hanya bila tidak ada penanda baruh LLM dipakai.
    deterministic = classify_mood(text)
    if deterministic.is_confident:
        return deterministic.category

    if llm_post is None or llm_config is None:
        return deterministic.category

    llm_mood = classify_mood_llm(text, post=llm_post, config=llm_config)
    # Kategori deterministik tidak ditawarkan ke LLM supaya tidak ada dua
    # sumber yang saling bertentangan untuk hal yang sama.
    if llm_mood in LLM_MOOD_CATEGORIES:
        return llm_mood
    return deterministic.category


def sanitize_user_direction(text: str | None) -> str:
    """Bersihkan arahan pengguna dari upaya menyuntikkan struktur prompt.

    Buang token placeholder, buang pagar pembatas, batasi panjang. Ini bukan
    pertahanan sempurna — tidak ada yang sempurna untuk teks bebas — tetapi
    menutup jalur yang paling mudah dan paling merusak.
    """
    if not text:
        return ""

    cleaned = _PLACEHOLDER_RE.sub("", text)
    cleaned = _DELIMITER_RE.sub("", cleaned)
    cleaned = cleaned.strip()

    if len(cleaned) > MAX_USER_DIRECTION_CHARS:
        cleaned = cleaned[:MAX_USER_DIRECTION_CHARS].rstrip() + " ..."

    return cleaned


def _clock_to_seconds(value: str) -> float:
    """Ubah "2:50" menjadi 170,0 dan "1:05:00" menjadi 3900,0."""
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        return float(parts[0] * 60 + parts[1])
    return float(parts[0] * 3600 + parts[1] * 60 + parts[2])


def parse_requested_ranges(direction: str) -> list[tuple[float, float]]:
    """Ambil rentang waktu yang disebut pengguna, dalam detik.

    Rentang ini dikecualikan dari saringan durasi minimum: bila pengguna meminta
    "2:00 - 2:50", klip 50 detik adalah jawaban yang benar, bukan yang ditolak.
    """
    ranges: list[tuple[float, float]] = []
    for raw_start, raw_end in _TIME_RANGE_RE.findall(sanitize_user_direction(direction)):
        ranges.append((_clock_to_seconds(raw_start), _clock_to_seconds(raw_end)))
    return ranges


def strip_markdown_fences(text: str) -> str:
    """Buang pagar ```json yang sering ditambahkan model."""
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 :]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


def recover_json_objects(text: str) -> tuple[list[dict[str, Any]], int]:
    """Selamatkan objek JSON dari keluaran yang rusak sebagian.

    Model rutin menghasilkan satu tanda kutip tak ter-escape di dalam satu
    string, dan itu membuat ``json.loads`` menolak **seluruh** array — termasuk
    sepuluh segmen yang sebenarnya baik. Di sini kita menelusuri teks dan
    mengambil setiap objek yang dapat diurai.

    ``raw_decode`` dipakai (bukan ``loads`` bertingkat) karena ia berhenti tepat
    di akhir satu nilai dan memberi tahu posisinya, sehingga penelusuran bisa
    dilanjutkan setelah objek yang rusak.

    Returns:
        ``(objects, skipped)`` — jumlah yang dilewati dilaporkan supaya
        kehilangan data tidak terjadi diam-diam.
    """
    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    skipped = 0
    index = 0
    length = len(text)

    while index < length:
        if text[index] != "{":
            index += 1
            continue
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            # Objek ini rusak; lanjut mencari objek berikutnya.
            skipped += 1
            index += 1
            continue
        if isinstance(value, dict):
            objects.append(value)
        index = end

    return objects, skipped


def build_prompt(
    *,
    transcript: str,
    target_count: int,
    user_direction: str = "",
    output_language: str = "Bahasa Indonesia",
    version: str = SCORING_PROMPT_VERSION,
) -> list[dict[str, str]]:
    """Susun pesan untuk model, dengan rubrik penilaian eksplisit.

    Rubrik (definisi hook / completeness / emotional_arc) tidak lagi ditulis di
    sini: ia hidup di :mod:`clipper_shared.scoring_prompt` supaya bisa di-tuning
    dan berversi tanpa menyentuh logika pemanggil.

    Susunannya penting: **transkrip lebih dulu, arahan pengguna terakhir**.
    Transkrip podcast 60 menit dapat melewati 100 ribu karakter, dan model
    memberi bobot lebih besar pada bagian akhir — arahan yang diletakkan sebelum
    transkrip akan diabaikan.
    """
    return build_scoring_prompt(
        transcript=transcript,
        target_count=target_count,
        min_s=MIN_SEGMENT_S,
        max_s=MAX_SEGMENT_S,
        user_direction=sanitize_user_direction(user_direction),
        output_language=output_language,
        version=version,
    )


def _clamp(value: Any, low: float, high: float, fallback: float) -> float:
    """Batasi nilai numerik ke rentang, dengan nilai cadangan bila bukan angka."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def normalize_segments(
    raw_objects: list[dict[str, Any]],
    *,
    requested_ranges: list[tuple[float, float]] | None = None,
    limit: int | None = None,
    max_end_s: float | None = None,
    words: list[dict[str, Any]] | None = None,
    llm_post: Any = None,
    llm_config: Any = None,
) -> list[ScoredSegment]:
    """Ubah objek mentah menjadi segmen tervalidasi.

    Aturan:

    * durasi disaring, KECUALI untuk rentang yang diminta pengguna eksplisit;
    * segmen tumpang tindih dibuang — yang skornya lebih rendah kalah;
    * nilai dinormalkan ke rentang yang benar;
    * bila ``max_end_s`` diberikan, segmen yang mulai di luar video dibuang dan
      akhir segmen dipotong ke durasi video (model kadang berhalusinasi waktu);
    * bila ``limit`` diberikan, hanya segmen berskor tertinggi sebanyak itu yang
      dipertahankan;
    * hasil diurutkan menaik berdasarkan waktu.

    Args:
        raw_objects: objek mentah hasil parsing keluaran LLM.
        requested_ranges: rentang waktu yang diminta pengguna; dikecualikan
            dari saringan durasi minimum.
        limit: jumlah maksimum segmen yang dikembalikan. Pemanggil meminta
            sedikit lebih banyak kandidat ke model sebagai cadangan (sebagian
            gugur karena durasi/tumpang tindih), lalu memangkasnya di sini agar
            hasil akhir sesuai permintaan pengguna — tanpa ini, pengguna yang
            meminta 3 klip bisa mendapat 8.
    """
    ranges = requested_ranges or []
    candidates: list[ScoredSegment] = []

    for obj in raw_objects:
        if not isinstance(obj, dict):
            continue

        # Model kadang memakai nama field yang berbeda antar penyedia.
        start = obj.get("start_s", obj.get("start"))
        end = obj.get("end_s", obj.get("end"))
        if start is None or end is None:
            continue

        try:
            start_s = float(start)
            end_s = float(end)
        except (TypeError, ValueError):
            continue

        if start_s < 0:
            continue
        if max_end_s is not None:
            if start_s >= max_end_s:
                continue
            end_s = min(end_s, max_end_s)
        if end_s <= start_s:
            continue

        duration = end_s - start_s
        requested = any(
            abs(start_s - range_start) <= RANGE_MATCH_TOLERANCE_S
            and abs(end_s - range_end) <= RANGE_MATCH_TOLERANCE_S
            for range_start, range_end in ranges
        )
        if not requested and not (MIN_SEGMENT_S <= duration <= MAX_SEGMENT_S):
            continue

        # Skor bisa 0-1 atau 0-100 tergantung model; dinormalkan ke 0-100.
        raw_score = _clamp(obj.get("score", 0.5), 0.0, 100.0, 50.0)
        score = raw_score * 100.0 if raw_score <= 1.0 else raw_score

        candidates.append(
            ScoredSegment(
                start_s=start_s,
                end_s=end_s,
                score=round(score, 2),
                mood=_mood_for(start_s, end_s, words, llm_post=llm_post, llm_config=llm_config),
                # Dipotong ke MAX_LABEL_CHARS, BUKAN angka bebas. Kolom
                # ``segments.label`` adalah varchar(64) dan model Pydantic di
                # clipper_shared.scoring juga membatasi 64; nilai 80 di sini
                # melewati keduanya karena ScoredSegment adalah dataclass biasa
                # (tanpa validasi), lalu INSERT gagal dengan
                # StringDataRightTruncation. Terbukti saat pengujian end-to-end.
                label=str(obj.get("label") or obj.get("title") or "Segmen").strip()[
                    :MAX_LABEL_CHARS
                ],
                hook_score=_clamp(obj.get("hook_score"), 0.0, 1.0, 0.5),
                completeness=_clamp(obj.get("completeness"), 0.0, 1.0, 0.5),
                emotional_arc=_clamp(obj.get("emotional_arc"), 0.0, 1.0, 0.5),
                reason=str(obj.get("description") or obj.get("reason") or "").strip()[:400],
            )
        )

    # Buang tumpang tindih: skor tertinggi diutamakan.
    candidates.sort(key=lambda segment: segment.score, reverse=True)
    accepted: list[ScoredSegment] = []
    for candidate in candidates:
        overlaps = any(
            candidate.start_s < kept.end_s and candidate.end_s > kept.start_s
            for kept in accepted
        )
        if not overlaps:
            accepted.append(candidate)

    # Pangkas ke jumlah yang diminta. ``accepted`` saat ini terurut menurun
    # berdasarkan skor (warisan pengurutan di atas), jadi memotong dari depan
    # mempertahankan kandidat terbaik.
    if limit is not None and len(accepted) > limit:
        accepted = accepted[:limit]

    accepted.sort(key=lambda segment: segment.start_s)
    return accepted


def _render_transcript(words: list[dict[str, Any]]) -> str:
    """Ubah daftar kata menjadi teks bertimestamp untuk prompt.

    Dibuat berkelompok per baris agar model dapat merujuk waktu dengan mudah.
    Tidak dipotong di sini: ``score_job`` membandingkannya dengan kapasitas
    konteks model dan gagal dengan pesan jelas, alih-alih diam-diam membuang
    bagian akhir video.
    """
    lines: list[str] = []
    bucket_start: float | None = None
    bucket: list[str] = []

    for word in words:
        text = str(word.get("text") or "").strip()
        if not text:
            continue
        start = float(word.get("start_s") or 0.0)

        if bucket_start is None:
            bucket_start = start

        bucket.append(text)

        # Baris baru setiap ~12 detik: cukup rapat untuk merujuk waktu presisi,
        # cukup jarang agar prompt tetap ringkas.
        if start - bucket_start >= 12.0:
            lines.append(f"[{bucket_start:.1f}s] {' '.join(bucket)}")
            bucket = []
            bucket_start = None

    if bucket and bucket_start is not None:
        lines.append(f"[{bucket_start:.1f}s] {' '.join(bucket)}")

    return "\n".join(lines)


def _extract_content(raw_body: str) -> str:
    """Ambil teks jawaban dari respons bergaya OpenAI."""
    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError:
        return ""

    if not isinstance(data, dict):
        return ""

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
            text = first.get("text")
            if isinstance(text, str):
                return text

    for key in ("output_text", "content", "response"):
        value = data.get(key)
        if isinstance(value, str):
            return value

    return ""


def score_job(
    *,
    words: list[dict[str, Any]],
    target_count: int,
    provider: ProviderConfig | None = None,
    user_direction: str = "",
    allow_private: bool = False,
    timeout_s: float = 180.0,
) -> ScoringOutcome:
    """Jalankan skoring untuk satu transkrip.

    Melempar hanya untuk kesalahan yang tidak dapat dipulihkan. Kegagalan
    jaringan dan keluaran model yang rusak dikembalikan sebagai
    ``ScoringOutcome`` dengan ``error`` terisi, supaya pemanggil dapat memutuskan
    apakah layak di-retry.
    """
    if provider is None:
        # Default aman bila pemanggil tidak menyertakan konfigurasi: resolve_provider
        # akan mengisi base_url/model dari preset "gemini", sama seperti saat
        # pemanggil lama meneruskan dict kosong.
        provider = ProviderConfig(
            preset="gemini",
            base_url="",
            model="",
            api_key="",
            allow_private_host=False,
            default_direction="",
            context_tokens=None,
        )
    try:
        config: AIProviderConfig = resolve_provider(
            preset=provider["preset"],
            base_url=provider["base_url"],
            model=provider["model"],
            api_key=provider["api_key"],
            allow_private=allow_private,
        )
    except ValueError as exc:
        return ScoringOutcome(error=f"Konfigurasi penyedia AI tidak sah: {exc}")

    direction = sanitize_user_direction(user_direction)
    ranges = parse_requested_ranges(direction)

    transcript = _render_transcript(words)
    budget = transcript_char_budget(provider.get("context_tokens"))
    if len(transcript) > budget:
        return ScoringOutcome(
            error=(
                f"Transkrip ({len(transcript):,} karakter) melebihi kapasitas konteks "
                f"model AI (±{budget:,} karakter). Pakai model berkonteks lebih besar "
                "atau video yang lebih pendek."
            ),
            provider_label=config.preset.value,
        )

    # Minta lebih banyak daripada yang dibutuhkan: sebagian akan tersaring
    # karena durasi atau tumpang tindih, sehingga tidak perlu panggilan kedua.
    messages = build_prompt(
        transcript=transcript,
        target_count=target_count + 3,
        user_direction=direction,
    )

    headers = {"content-type": "application/json"}
    if config.api_key:
        headers["authorization"] = f"Bearer {config.api_key}"

    body = {
        "model": config.model,
        "messages": messages,
        # Temperature menurut niat (TECH_SPEC §5.4).
        "temperature": 0.3 if direction else 1.0,
        "response_format": {"type": "json_object"},
    }

    label = config.preset.value
    response: httpx.Response | None = None
    error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=timeout_s) as client:
                response = client.post(config.chat_completions_url, headers=headers, json=body)
        except httpx.TimeoutException:
            response, error = None, "Penyedia AI tidak merespons tepat waktu."
        except httpx.HTTPError as exc:
            response, error = None, f"Tidak dapat menghubungi penyedia AI: {exc}"

        # Hanya kegagalan sementara yang diulang; 4xx lain (kunci salah, model
        # tidak ada) akan gagal sama persis pada percobaan berikutnya.
        transient = response is None or response.status_code == 429 or response.status_code >= 500
        if not transient or attempt == MAX_ATTEMPTS:
            break
        delay = RETRY_BASE_DELAY_S * 2 ** (attempt - 1)
        reason = error if response is None else f"HTTP {response.status_code}"
        logger.warning("Skoring percobaan %d gagal (%s); ulang dalam %.0f detik.", attempt, reason, delay)
        time.sleep(delay)

    if response is None:
        return ScoringOutcome(error=f"{error} ({MAX_ATTEMPTS} percobaan)", provider_label=label)

    # Kesalahan penyedia dibedakan dari kesalahan masukan: tindakannya berbeda.
    if response.status_code in {401, 403}:
        return ScoringOutcome(
            error="API key penyedia AI ditolak. Periksa konfigurasi penyedia.",
            provider_label=label,
        )
    if response.status_code == 429:
        return ScoringOutcome(
            error="Kuota penyedia AI habis. Coba lagi nanti atau ganti penyedia.",
            provider_label=label,
        )
    if response.status_code >= 500:
        return ScoringOutcome(
            error="Penyedia AI sedang bermasalah. Coba lagi atau ganti penyedia.",
            provider_label=label,
        )
    if response.status_code >= 400:
        return ScoringOutcome(
            error=f"Penyedia AI menolak permintaan (HTTP {response.status_code}).",
            provider_label=label,
        )

    content = _extract_content(response.text)
    if not content:
        return ScoringOutcome(
            error="Penyedia AI membalas tanpa isi yang dapat dibaca.",
            provider_label=label,
        )

    cleaned = strip_markdown_fences(content)
    objects, skipped = recover_json_objects(cleaned)

    # Sebagian penyedia membalas satu objek berisi array segmen: biasanya di
    # kunci "segments", tetapi model kadang memakai nama lain ("clips", ...).
    if len(objects) == 1:
        lists = [value for value in objects[0].values() if isinstance(value, list)]
        if lists:
            objects = [item for item in lists[0] if isinstance(item, dict)]

    if skipped:
        logger.warning("Melewati %d objek JSON yang rusak saat parsing skor.", skipped)

    max_end = max((float(w.get("end_s") or 0.0) for w in words), default=0.0) or None
    return ScoringOutcome(
        segments=normalize_segments(
            objects,
            requested_ranges=ranges,
            limit=target_count,
            max_end_s=max_end,
            words=words,
            llm_post=_post_chat,
            llm_config=config,
        ),
        recovered_count=skipped,
        provider_label=label,
        raw_length=len(content),
    )


def chunk_words(
    words: list[dict[str, Any]], *, min_s: float = 30.0, max_s: float = 60.0
) -> list[tuple[float, float]]:
    """Bagi transkrip menjadi potongan ``min_s``–``max_s`` detik.

    Potongan diakhiri pada jeda bicara >= 2 detik bila ada; bila pembicara terus
    bicara tanpa jeda, potongan dipaksa berakhir sebelum melewati ``max_s``
    supaya tidak ada satu potongan pun yang mencakup seluruh video.
    """
    if not words:
        return []

    def num(value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0.0
        return float(value)

    chunks: list[tuple[float, float]] = []
    start = num(words[0].get("start_s"))
    previous_end = start
    for word in words:
        word_start, word_end = num(word.get("start_s")), num(word.get("end_s"))
        long_enough = previous_end - start >= min_s
        if long_enough and (word_start - previous_end >= 2.0 or word_end - start > max_s):
            chunks.append((start, previous_end))
            start = word_start
        previous_end = max(previous_end, word_end)
    if previous_end - start >= min_s:
        chunks.append((start, previous_end))
    return chunks


def heuristic_segments(words: list[dict[str, Any]], target_count: int) -> list[ScoredSegment]:
    """Kandidat cadangan tanpa LLM: potongan dengan bicara paling padat.

    Dipakai bila penyedia AI gagal, supaya satu gangguan penyedia tidak
    menghentikan seluruh pipeline. Kepadatan kata per detik adalah sinyal kasar
    tetapi murah: bagian hening atau musik jarang layak dijadikan klip.
    """
    scored: list[ScoredSegment] = []
    for index, (start, end) in enumerate(chunk_words(words)):
        count = sum(1 for w in words if start <= float(w.get("start_s") or 0.0) < end)
        density = count / max(end - start, 1.0)
        scored.append(
            ScoredSegment(
                start_s=start,
                end_s=end,
                score=round(min(100.0, density * 25.0), 2),
                label=f"Segmen otomatis #{index + 1}",
                hook_score=0.5,
                completeness=0.5,
                emotional_arc=0.5,
                reason="Dipilih tanpa AI berdasarkan kepadatan bicara.",
                mood=_mood_for(start, end, words),
            )
        )
    scored.sort(key=lambda segment: segment.score, reverse=True)
    return sorted(scored[:target_count], key=lambda segment: segment.start_s)
