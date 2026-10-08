"""Distilasi pola dari korpus klip viral menjadi usulan rubrik (mekanisme 3).

**Apa yang dilakukan skrip ini.** Membaca korpus klip yang sudah dikumpulkan,
mengirim transkripnya ke LLM dengan pertanyaan yang sangat spesifik — pola apa
yang berulang di momen pembuka dan penutup klip-klip ini — lalu menghasilkan
**usulan teks rubrik**. Hasilnya ditulis ke berkas, BUKAN langsung diterapkan.

**Mengapa tidak langsung diterapkan.** Ini keputusan desain yang disengaja.
Rubrik yang hidup di :mod:`clipper_shared.scoring_prompt` dipakai produksi;
menggantinya otomatis dari keluaran LLM berarti kualitas produksi ditentukan
oleh satu balasan tanpa tinjauan. Skrip ini hanya menghasilkan draf untuk
dibaca manusia.

**Peringatan terpenting — jangan dilatih pada sinyal palsu.** Agent memilih
momen **hanya dari transkrip**. Yang membuat klip viral sebagian besar ada di
LUAR transkrip: hook visual, nada bicara, musik, algoritma, dan jumlah
follower yang sudah dimiliki channel. Jadi skrip ini tidak pernah mengklaim
"belajar virality" — ia hanya mengekstrak **pola teks** yang bisa ditiru.
Semua klaim lebih dari itu harus diukur dengan ``eval_scoring.py``.

**Syarat korpus yang benar** (ini yang paling sering salah):

1. Satuan harus **klip**, bukan video utuh. Melabel video 60 menit sebagai
   "viral" tidak memberi tahu model momen MANA yang bagus.
2. Label jangan view count mentah — itu terkontaminasi ukuran channel. Pakai
   rasio terhadap median channel itu sendiri.

Pemakaian::

    .venv-win\\Scripts\\python.exe scripts\\distill_viral_patterns.py --dry-run
    .venv-win\\Scripts\\python.exe scripts\\distill_viral_patterns.py
    .venv-win\\Scripts\\python.exe scripts\\distill_viral_patterns.py --out .work/draft.md
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

#: Akar repo: ``<repo>/scripts/distill_viral_patterns.py``.
REPO_ROOT = Path(__file__).resolve().parents[1]

for _entry in ("apps/worker-render", "packages/shared/src", "apps/worker-light", "apps/api"):
    _path = str(REPO_ROOT / _entry)
    if _path not in sys.path:
        sys.path.insert(0, _path)

import httpx  # noqa: E402
from clipper_shared.ai_provider import resolve_provider  # noqa: E402

logger = logging.getLogger("distill")

#: Direktori korpus.
DEFAULT_CORPUS = REPO_ROOT / "eval" / "viral" / "clips"

#: Berkas keluaran draf rubrik.
DEFAULT_OUT = REPO_ROOT / "eval" / "viral" / "rubrik-usulan.md"

#: Jumlah klip yang dikirim per panggilan. Lebih sedikit = analisis lebih
#: dalam per klip, tetapi lebih banyak panggilan.
CLIPS_PER_BATCH = 12

#: Potongan transkrip per klip, karakter. Pembuka dan penutup yang dianalisis;
#: bagian tengah jarang menentukan apakah klipnya menahan penonton.
HEAD_CHARS = 900
TAIL_CHARS = 400

#: Maksimum karakter korpus yang dikirim per panggilan, pagar biaya.
MAX_BATCH_CHARS = 30_000


@dataclass(frozen=True, slots=True)
class Clip:
    """Satu klip dalam korpus.

    Attributes:
        slug: Pengenal klip.
        transcript: Teks transkrip klip (tanpa timestamp juga boleh).
        note: Catatan bebas, misal topik atau alasan masuk korpus.
    """

    slug: str
    transcript: str
    note: str = ""


#: Pola baris bertimestamp ``[12.4s] teks ...``
_LINE_RE = re.compile(r"^\[\d+(?:\.\d+)?s\]\s*(.*)$")


def _strip_timestamps(transcript: str) -> str:
    """Buang awalan timestamp agar teksnya bersih untuk dianalisis.

    Args:
        transcript: Teks transkrip, boleh bertimestamp.

    Returns:
        Teks tanpa awalan ``[12.4s]``.
    """
    lines: list[str] = []
    for line in transcript.splitlines():
        match = _LINE_RE.match(line.strip())
        lines.append(match.group(1) if match else line.strip())
    return " ".join(line for line in lines if line)


def load_corpus(directory: Path) -> list[Clip]:
    """Baca semua klip dalam direktori korpus.

    Format berkas: JSON dengan field ``slug``, ``transcript``, dan opsional
    ``note``. Berkas ``*.example`` dilewati.

    Args:
        directory: Direktori korpus.

    Returns:
        Daftar klip, urut menaik berdasar nama berkas.
    """
    if not directory.is_dir():
        return []

    clips: list[Clip] = []
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Lewati %s: %s", path.name, exc)
            continue
        if not isinstance(raw, dict):
            logger.warning("Lewati %s: akar bukan objek.", path.name)
            continue
        transcript = str(raw.get("transcript") or "").strip()
        if not transcript:
            logger.warning("Lewati %s: transkrip kosong.", path.name)
            continue
        clips.append(
            Clip(
                slug=str(raw.get("slug") or path.stem),
                transcript=transcript,
                note=str(raw.get("note") or ""),
            )
        )
    return clips


def build_batch_prompt(clips: list[Clip], *, output_language: str) -> list[dict[str, str]]:
    """Susun prompt distilasi untuk satu kelompok klip.

    Pertanyaannya sengaja dibatasi ke **pola teks**, bukan "kenapa ini viral",
    karena jawaban atas pertanyaan kedua akan menghasilkan omong kosong
    (algoritma, follower, waktu posting) yang tidak bisa ditindaklanjuti dari
    transkrip.

    Args:
        clips: Klip dalam kelompok ini.
        output_language: Bahasa keluaran.

    Returns:
        Daftar pesan gaya OpenAI.
    """
    system = (
        "Anda menganalisis kumpulan transkrip klip pendek yang terbukti "
        "berkinerja baik. Tugas Anda BUKAN menebak kenapa mereka viral — "
        "algoritma, jumlah pengikut, dan waktu tayang tidak terlihat di "
        "transkrip, dan menebaknya hanya menghasilkan omong kosong.\n\n"
        "Tugas Anda: ekstrak POLA TEKS yang berulang, khususnya pada:\n"
        "1. Tiga detik pertama (pembuka) — bagaimana kalimatnya dimulai?\n"
        "2. Bagian penutup — apakah gagasannya selesai, atau menggantung?\n"
        "3. Struktur — adakah setup dan payoff di dalam rentang yang pendek?\n\n"
        "Keluaran HARUS berupa JSON valid, tanpa pagar markdown:\n"
        '{"patterns": [{"name": "<nama singkat>", "evidence": "<contoh nyata '
        'dari transkrip>", "where": "hook|ending|structure", "actionable": '
        '"<apa yang harus dilakukan pemilih momen>"}], '
        '"counter_patterns": [{"name": "...", "evidence": "..."}], '
        '"notes": "<catatan singkat, termasuk pola yang TIDAK bisa ditiru '
        'dari teks>"}\n\n'
        "Aturan:\n"
        "* hanya laporkan pola yang muncul di LEBIH DARI SATU klip;\n"
        "* 'evidence' harus kutipan nyata dari transkrip yang diberikan;\n"
        "* 'actionable' harus bisa dilakukan hanya dengan membaca transkrip;\n"
        "* jangan mengarang; bila tidak ada pola yang berulang, kembalikan "
        "daftar kosong."
    )

    blocks: list[str] = []
    for clip in clips:
        text = _strip_timestamps(clip.transcript)
        head = text[:HEAD_CHARS]
        tail = text[-TAIL_CHARS:] if len(text) > HEAD_CHARS + TAIL_CHARS else ""
        body = head + ("\n...\n[AKHIR] " + tail if tail else "")
        note = f" ({clip.note})" if clip.note else ""
        blocks.append(f"=== KLIP {clip.slug}{note} ===\n{body}")

    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"Analisis {len(clips)} klip berikut. Tulis jawaban dalam {output_language}.\n\n"
                + "\n\n".join(blocks)
            ),
        },
    ]


def _post(config, messages: list[dict[str, str]], *, timeout_s: float) -> str:
    """Kirim satu permintaan dan kembalikan isinya.

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
        logger.warning("Panggilan distilasi gagal: %s", exc)
        return ""
    if response.status_code >= 400:
        logger.warning("Penyedia membalas HTTP %s.", response.status_code)
        return ""

    from worker_light.scoring_client import _extract_content

    return _extract_content(response.text)


def _parse_patterns(text: str) -> dict[str, object]:
    """Ambil objek JSON pertama yang valid dari balasan.

    Args:
        text: Balasan mentah.

    Returns:
        Kamus hasil; kosong bila tidak ada yang bisa diurai.
    """
    from worker_light.scoring_client import recover_json_objects

    objects, _ = recover_json_objects(text)
    return objects[0] if objects else {}


def _render_markdown(results: list[dict[str, object]], *, corpus_len: int) -> str:
    """Ubah hasil distilasi menjadi draf rubrik yang bisa dibaca manusia.

    Args:
        results: Hasil per kelompok.
        corpus_len: Jumlah klip dalam korpus.

    Returns:
        Teks markdown draf.
    """
    lines: list[str] = [
        "# Usulan rubrik dari korpus viral (DRAF — belum diterapkan)",
        "",
        f"Dihasilkan dari {corpus_len} klip. **Tinjau sebelum dipakai.**",
        "",
        "Berkas ini DRAF. Ia tidak mengubah apa pun di produksi. Untuk menerapkan,",
        "pindahkan pola yang relevan ke `clipper_shared.scoring_prompt`, lalu ukur",
        "dengan `scripts/eval_scoring.py` — tanpa itu, tidak ada bukti pola ini",
        "membantu.",
        "",
        "## Pola yang berulang",
        "",
    ]

    patterns: list[dict[str, object]] = []
    counter: list[dict[str, object]] = []
    notes: list[str] = []
    for result in results:
        for item in result.get("patterns") or []:
            if isinstance(item, dict):
                patterns.append(item)
        for item in result.get("counter_patterns") or []:
            if isinstance(item, dict):
                counter.append(item)
        if isinstance(result.get("notes"), str) and result["notes"].strip():
            notes.append(result["notes"].strip())

    if not patterns:
        lines.append("_Tidak ada pola yang dilaporkan berulang._")
    for item in patterns:
        lines.append(f"### {item.get('name', '(tanpa nama)')} — `{item.get('where', '?')}`")
        lines.append("")
        lines.append(f"- **Bukti:** {item.get('evidence', '-')}")
        lines.append(f"- **Tindakan:** {item.get('actionable', '-')}")
        lines.append("")

    lines.append("## Pola yang justru dihindari")
    lines.append("")
    if not counter:
        lines.append("_Tidak dilaporkan._")
    for item in counter:
        lines.append(f"- **{item.get('name', '(tanpa nama)')}:** {item.get('evidence', '-')}")
    lines.append("")

    lines.append("## Catatan (termasuk yang TIDAK bisa ditiru dari teks)")
    lines.append("")
    if not notes:
        lines.append("_Tidak ada catatan._")
    for note in notes:
        lines.append(f"- {note}")
    lines.append("")

    return "\n".join(lines)


def distill(
    *,
    corpus: Path,
    out: Path,
    dry_run: bool,
    timeout_s: float,
    output_language: str,
) -> int:
    """Jalankan distilasi.

    Args:
        corpus: Direktori korpus.
        out: Berkas keluaran draf.
        dry_run: Bila ``True``, hanya tampilkan prompt tanpa memanggil penyedia.
        timeout_s: Batas waktu per panggilan.
        output_language: Bahasa keluaran.

    Returns:
        Kode keluar proses.
    """
    clips = load_corpus(corpus)
    if not clips:
        print(
            f"Korpus kosong: {corpus}\n"
            "Isi dengan berkas JSON berfield 'transcript' (dan opsional 'slug'/'note').",
            file=sys.stderr,
        )
        return 2

    batches: list[list[Clip]] = []
    current: list[Clip] = []
    size = 0
    for clip in clips:
        if current and (len(current) >= CLIPS_PER_BATCH or size + len(clip.transcript) > MAX_BATCH_CHARS):
            batches.append(current)
            current, size = [], 0
        current.append(clip)
        size += len(clip.transcript)
    if current:
        batches.append(current)

    print(f"Korpus   : {len(clips)} klip, {len(batches)} kelompok")

    if dry_run:
        messages = build_batch_prompt(batches[0], output_language=output_language)
        chars = sum(len(message["content"]) for message in messages)
        print(f"Dry-run  : {chars:,} karakter pada kelompok pertama")
        print("--- SYSTEM ---")
        print(messages[0]["content"])
        return 0

    api_key = os.getenv("AI_API_KEY", "").strip()
    model = os.getenv("AI_MODEL", "").strip()
    if not api_key or not model:
        print("AI_API_KEY dan AI_MODEL wajib diisi.", file=sys.stderr)
        return 2
    base_url = os.getenv(
        "AI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"
    ).strip()

    try:
        config = resolve_provider(
            preset=os.getenv("AI_PRESET", "gemini").strip(),
            base_url=base_url,
            model=model,
            api_key=api_key,
            allow_private=os.getenv("AI_ALLOW_LOCAL", "false").strip().lower() == "true",
        )
    except ValueError as exc:
        print(f"Konfigurasi penyedia tidak sah: {exc}", file=sys.stderr)
        return 2

    results: list[dict[str, object]] = []
    for index, batch in enumerate(batches, start=1):
        print(f"  menganalisis kelompok {index}/{len(batches)}...")
        content = _post(
            config, build_batch_prompt(batch, output_language=output_language), timeout_s=timeout_s
        )
        if not content:
            logger.warning("Kelompok %s tidak menghasilkan apa pun.", index)
            continue
        parsed = _parse_patterns(content)
        if parsed:
            results.append(parsed)

    if not results:
        print("Tidak ada hasil yang bisa diurai.", file=sys.stderr)
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        _render_markdown(results, corpus_len=len(clips)) + "\n", encoding="utf-8"
    )
    print()
    print(f"Draf ditulis: {out}")
    print("Tinjau dulu; jangan terapkan sebelum diukur dengan eval_scoring.py.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Titik masuk CLI.

    Args:
        argv: Argumen baris perintah; ``None`` berarti memakai ``sys.argv``.

    Returns:
        Kode keluar proses.
    """
    parser = argparse.ArgumentParser(
        description="Distilasi pola korpus viral menjadi draf rubrik."
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS, help="Direktori korpus.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Berkas draf keluaran.")
    parser.add_argument("--dry-run", action="store_true", help="Tampilkan prompt saja.")
    parser.add_argument("--timeout", type=float, default=180.0, help="Batas waktu per panggilan.")
    parser.add_argument("--language", default="Bahasa Indonesia", help="Bahasa keluaran.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    return distill(
        corpus=args.corpus,
        out=args.out,
        dry_run=args.dry_run,
        timeout_s=args.timeout,
        output_language=args.language,
    )


if __name__ == "__main__":
    raise SystemExit(main())
