"""Agent pemilihan momen: tiga pass LLM dengan pagar deterministik.

Nama "agent" di sini **sengaja sempit**: struktur langkahnya ada di kode, bukan
diputuskan model. Model hanya dipanggil di tiga titik keputusan (usulkan,
nilai, kritik). Alasan:

1. **Biaya harus bisa diprediksi.** Loop otonom yang menentukan langkahnya
   sendiri bisa memanggil model puluhan kali per job; dengan slot dispatcher
   terbatas (``STT_SLOTS=1``), itu bisa membekukan API.
2. **Kegagalan harus bisa dilacak.** Tiga pass bernama jauh lebih mudah
   di-debug daripada satu jejak percakapan panjang.
3. **Sejalan dengan keputusan repo:** jangan percaya keluaran LLM. Tiap pass
   punya pagar: batas jumlah panggilan, validasi bentuk, dan fallback.

Alurnya::

    SCOUT  — bagi transkrip jadi jendela kecil, tandai kandidat, recall tinggi
    JUDGE  — nilai tiap kandidat dari teks utuhnya, presisi tinggi
    CRITIC — buang yang topiknya tumpang tindih atau tidak berdiri sendiri
    LALU   — snap batas ke jeda bicara + validasi deterministik yang sudah ada

Semua panggilan lewat :func:`clipper_shared.dispatcher` karena modul ini
dipanggil dari dalam task ``score_segments``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from clipper_shared.ai_provider import (
    AIProviderConfig,
    ProviderConfig,
    resolve_provider,
)
from clipper_shared.scoring import MAX_LABEL_CHARS
from clipper_shared.scoring_prompt import (
    AGENT_PROMPT_VERSION,
    build_critic_prompt,
    build_judge_prompt,
    build_scout_prompt,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AgentConfig",
    "MAX_CRITIC_CALLS",
    "MAX_JUDGE_CALLS",
    "MAX_SCOUT_CALLS",
    "run_moment_agent",
]

#: Lebar jendela SCOUT, detik. Cukup kecil agar model membaca bagian itu
#: sungguh-sungguh, cukup besar agar satu momen tidak terbelah dua jendela.
SCOUT_WINDOW_S = 300.0

#: Tumpang tindih antar jendela, detik — momen yang kebetulan jatuh di batas
#: jendela tetap utuh di salah satu jendela.
SCOUT_OVERLAP_S = 30.0

#: Pagar jumlah panggilan per pass. Ini yang menjamin biaya tetap terprediksi:
#: tanpa pagar, transkrip panjang bisa menghasilkan puluhan panggilan.
MAX_SCOUT_CALLS = 8
MAX_JUDGE_CALLS = 24
MAX_CRITIC_CALLS = 1

#: Kandidat yang diminta per jendela SCOUT. Sengaja sedikit: pass ini murah dan
#: boleh melewatkan, karena JUDGE yang memutuskan.
SCOUT_CANDIDATES_PER_WINDOW = 4

#: Ambang skor JUDGE. Di bawah ini kandidat dianggap tidak layak dan dibuang
#: sebelum masuk CRITIC, supaya CRITIC tidak menghabiskan panggilan untuk
#: memilah sampah.
JUDGE_REJECT_BELOW = 55

#: Durasi yang diizinkan, detik. Mengikuti ``scoring_client`` (bukan
#: ``clipper_shared.scoring``) karena ini batas yang dipakai prompt saat ini.
MIN_SEGMENT_S = 25.0
MAX_SEGMENT_S = 65.0

#: Jeda minimum yang dianggap batas wajar antar kalimat, detik.
GAP_S = 2.0

#: Pergeseran maksimum saat menjepit batas ke jeda bicara, detik. Lebih besar
#: dari ini berarti model menunjuk momen yang berbeda, dan lebih baik
#: mempertahankan apa yang model maksud.
MAX_SNAP_SHIFT_S = 3.0

#: Ambang jeda untuk memotong transkrip jadi jendela.
_WINDOW_MIN_S = 60.0


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """Pagar dan knop agent.

    Attributes:
        scout_window_s: Lebar jendela pass SCOUT.
        scout_overlap_s: Tumpang tindih antar jendela.
        max_scout_calls: Batas panggilan SCOUT.
        max_judge_calls: Batas panggilan JUDGE.
        max_critic_calls: Batas panggilan CRITIC.
        candidates_per_window: Kandidat per jendela SCOUT.
        reject_below: Ambang skor JUDGE.
        version: Versi strategi; direkam untuk jejak.
    """

    scout_window_s: float = SCOUT_WINDOW_S
    scout_overlap_s: float = SCOUT_OVERLAP_S
    max_scout_calls: int = MAX_SCOUT_CALLS
    max_judge_calls: int = MAX_JUDGE_CALLS
    max_critic_calls: int = MAX_CRITIC_CALLS
    candidates_per_window: int = SCOUT_CANDIDATES_PER_WINDOW
    reject_below: int = JUDGE_REJECT_BELOW
    version: str = AGENT_PROMPT_VERSION


@dataclass(slots=True)
class _CallStats:
    """Penghitung panggilan, untuk laporan dan pagar."""

    scout: int = 0
    judge: int = 0
    critic: int = 0
    failed: int = 0
    raw_chars: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Total panggilan yang dilakukan."""
        return self.scout + self.judge + self.critic

    def as_dict(self) -> dict[str, int]:
        """Salinan sebagai kamus, untuk dilaporkan."""
        return {
            "scout": self.scout,
            "judge": self.judge,
            "critic": self.critic,
            "failed": self.failed,
        }


def _words_in_range(
    words: list[dict[str, Any]], start_s: float, end_s: float
) -> list[dict[str, Any]]:
    """Ambil kata yang berada dalam rentang waktu.

    Args:
        words: Daftar kata bertimestamp.
        start_s: Awal rentang.
        end_s: Akhir rentang.

    Returns:
        Kata yang awalnya berada di dalam rentang.
    """
    return [
        word
        for word in words
        if start_s <= float(word.get("start_s") or 0.0) < end_s
    ]


def _render(words: list[dict[str, Any]], *, bucket_s: float = 12.0) -> str:
    """Render kata menjadi teks bertimestamp per bucket.

    Args:
        words: Daftar kata bertimestamp.
        bucket_s: Lebar bucket, detik.

    Returns:
        Teks berformat ``[12.4s] teks ...``.
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
        if start - bucket_start >= bucket_s:
            lines.append(f"[{bucket_start:.1f}s] {' '.join(bucket)}")
            bucket = []
            bucket_start = None

    if bucket and bucket_start is not None:
        lines.append(f"[{bucket_start:.1f}s] {' '.join(bucket)}")
    return "\n".join(lines)


def split_windows(
    words: list[dict[str, Any]], *, window_s: float, overlap_s: float, max_windows: int
) -> list[tuple[float, float]]:
    """Bagi transkrip menjadi jendela bertumpang tindih.

    Transkrip panjang dibagi agar tiap panggilan SCOUT membaca bagian yang
    kecil. Jendela yang terlalu pendek di akhir digabung ke jendela
    sebelumnya, supaya tidak ada panggilan yang membaca 5 detik.

    Args:
        words: Daftar kata bertimestamp.
        window_s: Lebar jendela, detik.
        overlap_s: Tumpang tindih antar jendela, detik.
        max_windows: Jumlah maksimum jendela (pagar biaya).

    Returns:
        Daftar rentang ``(start, end)``.
    """
    if not words:
        return []

    start = float(words[0].get("start_s") or 0.0)
    end = max(float(word.get("end_s") or 0.0) for word in words)
    if end - start <= _WINDOW_MIN_S:
        return [(start, end)]

    step = max(window_s - overlap_s, 1.0)
    windows: list[tuple[float, float]] = []
    cursor = start
    while cursor < end and len(windows) < max_windows:
        windows.append((cursor, min(cursor + window_s, end)))
        cursor += step

    # Sisa yang pendek digabung ke jendela terakhir agar tidak ada jendela
    # yang terlalu kecil untuk dinilai.
    if cursor < end and windows:
        last_start, _ = windows[-1]
        windows[-1] = (last_start, end)

    return windows


def snap_to_gap(
    words: list[dict[str, Any]],
    start_s: float,
    end_s: float,
    *,
    max_shift_s: float = MAX_SNAP_SHIFT_S,
) -> tuple[float, float]:
    """Jepit batas segmen ke jeda bicara terdekat.

    Model mengembalikan detik sembarang, sehingga klip sering mulai atau
    berhenti di tengah kata. Fungsi ini menggeser batas ke jeda >= ``GAP_S``.

    Args:
        words: Daftar kata bertimestamp.
        start_s: Awal usulan.
        end_s: Akhir usulan.
        max_shift_s: Pergeseran maksimum yang diizinkan per batas.

    Returns:
        Pasangan ``(start, end)`` yang sudah dijepit.
    """
    if not words:
        return start_s, end_s

    starts = sorted(float(word.get("start_s") or 0.0) for word in words)
    ends = sorted(float(word.get("end_s") or 0.0) for word in words)

    def nearest(target: float, candidates: list[float]) -> float:
        best = target
        best_delta = max_shift_s
        for value in candidates:
            delta = abs(value - target)
            if delta < best_delta:
                best_delta = delta
                best = value
        return best

    # Awal segmen sebaiknya jatuh pada awal sebuah kata; akhir pada akhir kata.
    new_start = nearest(start_s, [s for s in starts if abs(s - start_s) <= max_shift_s])
    new_end = nearest(end_s, [e for e in ends if abs(e - end_s) <= max_shift_s])

    if new_end <= new_start:
        return start_s, end_s
    return new_start, new_end


def _post_json(
    config: AIProviderConfig,
    body: dict[str, Any],
    *,
    timeout_s: float,
    stats: _CallStats,
) -> str:
    """Kirim satu permintaan ke penyedia dan ambil isinya.

    Args:
        config: Konfigurasi penyedia yang sudah di-resolve.
        body: Badan permintaan gaya OpenAI.
        timeout_s: Batas waktu, detik.
        stats: Pencatat panggilan; diperbarui di tempat.

    Returns:
        Isi balasan, atau string kosong bila gagal.
    """
    headers = {"content-type": "application/json"}
    if config.api_key:
        headers["authorization"] = f"Bearer {config.api_key}"

    try:
        with httpx.Client(timeout=timeout_s) as client:
            response = client.post(config.chat_completions_url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        stats.failed += 1
        logger.warning("Panggilan agent gagal: %s", exc)
        return ""

    if response.status_code >= 400:
        stats.failed += 1
        logger.warning("Penyedia membalas HTTP %s pada satu pass agent.", response.status_code)
        return ""

    from worker_light.scoring_client import _extract_content

    content = _extract_content(response.text)
    stats.raw_chars += len(content)
    return content


def _parse_first_object(text: str) -> dict[str, Any] | None:
    """Ambil objek JSON pertama yang valid dari teks.

    Args:
        text: Teks balasan mentah.

    Returns:
        Obek pertama yang dapat diurai, atau ``None``.
    """
    from worker_light.scoring_client import recover_json_objects

    objects, _ = recover_json_objects(text)
    return objects[0] if objects else None


def _scout(
    config: AIProviderConfig,
    words: list[dict[str, Any]],
    window: tuple[float, float],
    *,
    cfg: AgentConfig,
    timeout_s: float,
    stats: _CallStats,
) -> list[tuple[float, float, str]]:
    """Satu panggilan SCOUT untuk satu jendela.

    Args:
        config: Konfigurasi penyedia.
        words: Seluruh kata transkrip.
        window: Rentang jendela.
        cfg: Pengaturan agent.
        timeout_s: Batas waktu per panggilan.
        stats: Pencatat panggilan.

    Returns:
        Daftar ``(start, end, label)``.
    """
    window_words = _words_in_range(words, window[0], window[1])
    if not window_words:
        return []

    messages = build_scout_prompt(
        transcript=_render(window_words),
        max_candidates=cfg.candidates_per_window,
    )
    body = {
        "model": config.model,
        "messages": messages,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    content = _post_json(config, body, timeout_s=timeout_s, stats=stats)
    stats.scout += 1
    if not content:
        return []

    parsed = _parse_first_object(content)
    if parsed is None:
        return []

    # Model kadang meletakkan kandidat di kunci selain "candidates".
    raw_list = parsed.get("candidates")
    if not isinstance(raw_list, list):
        for value in parsed.values():
            if isinstance(value, list):
                raw_list = value
                break
    if not isinstance(raw_list, list):
        return []

    found: list[tuple[float, float, str]] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        start = item.get("start_s", item.get("start"))
        end = item.get("end_s", item.get("end"))
        if start is None or end is None:
            continue
        try:
            start_s = float(start)
            end_s = float(end)
        except (TypeError, ValueError):
            continue
        if end_s <= start_s or start_s < 0:
            continue
        if end_s - start_s > MAX_SEGMENT_S:
            end_s = start_s + MAX_SEGMENT_S
        label = str(item.get("label") or "").strip()[:MAX_LABEL_CHARS]
        found.append((start_s, end_s, label))

    return found


def _judge(
    config: AIProviderConfig,
    words: list[dict[str, Any]],
    candidate: tuple[float, float, str],
    *,
    cfg: AgentConfig,
    output_language: str,
    timeout_s: float,
    stats: _CallStats,
) -> dict[str, Any] | None:
    """Satu panggilan JUDGE untuk satu kandidat.

    Args:
        config: Konfigurasi penyedia.
        words: Seluruh kata transkrip.
        candidate: ``(start, end, label)`` hasil SCOUT.
        cfg: Pengaturan agent.
        output_language: Bahasa untuk label dan alasan.
        timeout_s: Batas waktu per panggilan.
        stats: Pencatat panggilan.

    Returns:
        Kamus segmen yang sudah dinilai, atau ``None`` bila gagal/tidak layak.
    """
    start_s, end_s, label = candidate

    # Perlebar sedikit sebelum mengambil teks: SCOUT sengaja memberi batas
    # kasar, dan JUDGE perlu melihat kalimat di sekitarnya untuk menentukan
    # batas yang wajar.
    pad = 6.0
    window_words = _words_in_range(words, max(0.0, start_s - pad), end_s + pad)
    if not window_words:
        return None

    messages = build_judge_prompt(
        candidate_transcript=_render(window_words, bucket_s=6.0),
        start_s=start_s,
        end_s=end_s,
        min_s=MIN_SEGMENT_S,
        max_s=MAX_SEGMENT_S,
        reject_below=cfg.reject_below,
        output_language=output_language,
    )
    body = {
        "model": config.model,
        "messages": messages,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    content = _post_json(config, body, timeout_s=timeout_s, stats=stats)
    stats.judge += 1
    if not content:
        return None

    parsed = _parse_first_object(content)
    if parsed is None:
        return None

    from worker_light.scoring_client import _clamp

    new_start = parsed.get("start_s", parsed.get("start"))
    new_end = parsed.get("end_s", parsed.get("end"))

    def _num(value: Any, fallback: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    start_s = _num(new_start, start_s)
    end_s = _num(new_end, end_s)
    if end_s <= start_s:
        return None

    # Pagar durasi: model boleh menggeser batas, tetapi tidak boleh keluar dari
    # rentang yang diizinkan.
    if end_s - start_s > MAX_SEGMENT_S:
        end_s = start_s + MAX_SEGMENT_S
    if end_s - start_s < MIN_SEGMENT_S:
        end_s = start_s + MIN_SEGMENT_S

    score = _clamp(parsed.get("score"), 0.0, 100.0, 0.0)
    if score < cfg.reject_below:
        return None

    return {
        "start_s": start_s,
        "end_s": end_s,
        "score": round(score, 2),
        "label": (str(parsed.get("label") or label).strip()[:MAX_LABEL_CHARS]) or "Tanpa label",
        "hook_score": round(_clamp(parsed.get("hook_score"), 0.0, 1.0, 0.5), 3),
        "completeness": round(_clamp(parsed.get("completeness"), 0.0, 1.0, 0.5), 3),
        "emotional_arc": round(_clamp(parsed.get("emotional_arc"), 0.0, 1.0, 0.5), 3),
        "reason": str(parsed.get("reason") or "").strip()[:400],
    }


def _critic(
    config: AIProviderConfig,
    judged: list[dict[str, Any]],
    *,
    timeout_s: float,
    stats: _CallStats,
) -> set[int]:
    """Satu panggilan CRITIC: indeks kandidat yang harus dibuang.

    Args:
        config: Konfigurasi penyedia.
        judged: Kandidat yang sudah dinilai, urut menurun berdasar skor.
        timeout_s: Batas waktu per panggilan.
        stats: Pencatat panggilan.

    Returns:
        Himpunan indeks yang dibuang. Kosong bila panggilan gagal — gagal di
        pass ini tidak boleh menghapus hasil kerja pass sebelumnya.
    """
    if len(judged) < 3:
        return set()

    lines: list[str] = []
    for index, item in enumerate(judged):
        lines.append(
            f"[{index}] {item['start_s']:.1f}-{item['end_s']:.1f}s "
            f"skor={item['score']:.0f} — {item['label']}: {item['reason']}"
        )

    messages = build_critic_prompt(candidates_text="\n".join(lines), keep_count=len(judged))
    body = {
        "model": config.model,
        "messages": messages,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    content = _post_json(config, body, timeout_s=timeout_s, stats=stats)
    stats.critic += 1
    if not content:
        return set()

    parsed = _parse_first_object(content)
    if parsed is None:
        return set()

    raw_drop = parsed.get("drop")
    if not isinstance(raw_drop, list):
        return set()

    dropped: set[int] = set()
    for value in raw_drop:
        if isinstance(value, bool):
            continue
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(judged):
            dropped.add(index)
    return dropped


def run_moment_agent(
    *,
    words: list[dict[str, Any]],
    target_count: int,
    provider: ProviderConfig,
    user_direction: str = "",
    allow_private: bool = False,
    timeout_s: float = 180.0,
    max_end_s: float | None = None,
    is_canceled: Callable[[], bool] | None = None,
    on_progress: Callable[[str], None] | None = None,
    config: AgentConfig | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Jalankan agent pemilihan momen tiga pass.

    Args:
        words: Kata bertimestamp hasil transkripsi.
        target_count: Jumlah segmen yang diinginkan.
        provider: Konfigurasi penyedia.
        user_direction: Arahan pengguna (disanitasi pemanggil).
        allow_private: Izinkan penyedia di jaringan lokal.
        timeout_s: Batas waktu per panggilan.
        max_end_s: Durasi video; dipakai memotong segmen liar.
        is_canceled: Fungsi ``() -> bool`` untuk titik pemeriksaan pembatalan.
        on_progress: Fungsi ``(message: str) -> None`` untuk laporan progres.
        config: Pengaturan agent; ``None`` berarti bawaan.

    Returns:
        Pasangan ``(segments, meta)``. ``segments`` bisa kosong bila semua pass
        gagal; pemanggil wajib punya fallback.
    """
    cfg = config or AgentConfig()
    stats = _CallStats()
    meta: dict[str, Any] = {"agent_version": cfg.version, "calls": {}, "error": ""}

    def progress(message: str) -> None:
        if on_progress is not None:
            on_progress(message)

    try:
        resolved: AIProviderConfig = resolve_provider(
            preset=provider["preset"],
            base_url=provider["base_url"],
            model=provider["model"],
            api_key=provider["api_key"],
            allow_private=allow_private,
        )
    except ValueError as exc:
        meta["error"] = f"Konfigurasi penyedia AI tidak sah: {exc}"
        return [], meta

    if not words:
        meta["error"] = "Transkrip kosong."
        return [], meta

    # --- PASS 1: SCOUT -----------------------------------------------------
    windows = split_windows(
        words,
        window_s=cfg.scout_window_s,
        overlap_s=cfg.scout_overlap_s,
        max_windows=cfg.max_scout_calls,
    )
    if not windows:
        meta["error"] = "Transkrip terlalu pendek untuk dibagi jendela."
        return [], meta

    candidates: list[tuple[float, float, str]] = []
    for index, window in enumerate(windows):
        if is_canceled is not None and is_canceled():
            meta["error"] = "dibatalkan"
            return [], meta
        progress(f"Memindai bagian {index + 1}/{len(windows)}...")
        candidates.extend(
            _scout(resolved, words, window, cfg=cfg, timeout_s=timeout_s, stats=stats)
        )

    if not candidates:
        meta["calls"] = stats.as_dict()
        meta["error"] = "Pass scout tidak menemukan kandidat."
        return [], meta

    # Kandidat yang sama sering muncul di dua jendela karena tumpang tindih.
    # Urutkan berdasar awal, lalu buang yang tumpang tindih besar.
    candidates.sort(key=lambda item: (item[0], item[1]))
    deduped: list[tuple[float, float, str]] = []
    for candidate in candidates:
        if deduped:
            previous = deduped[-1]
            overlap = min(previous[1], candidate[1]) - max(previous[0], candidate[0])
            shorter = min(candidate[1] - candidate[0], previous[1] - previous[0])
            if shorter > 0 and overlap / shorter > 0.5:
                continue
        deduped.append(candidate)

    # --- PASS 2: JUDGE ----------------------------------------------------
    judged: list[dict[str, Any]] = []
    for index, candidate in enumerate(deduped[: cfg.max_judge_calls]):
        if is_canceled is not None and is_canceled():
            meta["error"] = "dibatalkan"
            return [], meta
        progress(f"Menilai kandidat {index + 1}/{len(deduped[: cfg.max_judge_calls])}...")
        result = _judge(
            resolved,
            words,
            candidate,
            cfg=cfg,
            output_language="Bahasa Indonesia",
            timeout_s=timeout_s,
            stats=stats,
        )
        if result is not None:
            judged.append(result)

    if not judged:
        meta["calls"] = stats.as_dict()
        meta["error"] = "Pass judge menolak semua kandidat."
        return [], meta

    # --- PASS 3: CRITIC ---------------------------------------------------
    judged.sort(key=lambda item: item["score"], reverse=True)
    survivors = judged[: max(target_count * 2, target_count)]
    if cfg.max_critic_calls > 0:
        progress("Menyisir kandidat yang tumpang tindih...")
        dropped = _critic(resolved, survivors, timeout_s=timeout_s, stats=stats)
        if dropped:
            survivors = [
                item for index, item in enumerate(survivors) if index not in dropped
            ]

    # --- VALIDASI DETERMINISTIK -------------------------------------------
    final: list[dict[str, Any]] = []
    for item in survivors:
        # Klasifikasi suasana deterministik dari penanda reaksi di transkrip.
        from clipper_shared.mood import classify_mood

        segment_text = " ".join(
            str(word.get("text") or "")
            for word in words
            if item["start_s"] <= float(word.get("start_s") or 0.0) < item["end_s"]
        )
        item["mood"] = classify_mood(segment_text).category

        start_s, end_s = snap_to_gap(words, item["start_s"], item["end_s"])
        if end_s - start_s < MIN_SEGMENT_S:
            start_s, end_s = item["start_s"], item["end_s"]
        if max_end_s is not None:
            if start_s >= max_end_s:
                continue
            end_s = min(end_s, max_end_s)
        if end_s <= start_s:
            continue
        item["start_s"] = round(start_s, 2)
        item["end_s"] = round(end_s, 2)
        final.append(item)

    # Buang tumpang tindih yang masih tersisa setelah snapping; skor tinggi menang.
    final.sort(key=lambda item: item["score"], reverse=True)
    kept: list[dict[str, Any]] = []
    for item in final:
        if any(
            item["start_s"] < other["end_s"] and other["start_s"] < item["end_s"]
            for other in kept
        ):
            continue
        kept.append(item)
        if len(kept) >= target_count:
            break

    kept.sort(key=lambda item: item["start_s"])
    meta["calls"] = stats.as_dict()
    meta["raw_chars"] = stats.raw_chars
    meta["candidates_scouted"] = len(deduped)
    meta["candidates_judged"] = len(judged)
    return kept, meta
