"""Ukur kualitas pemilihan momen pada dataset berlabel.

Padanan ``eval_reframe.py`` untuk **pemilihan momen**. Yang diukur BUKAN
apakah AI-nya bekerja, melainkan apakah momen yang ia pilih cocok dengan
momen yang menurut manusia layak diklip:

1. Baca berkas kasus dari ``eval/scoring/cases/*.json`` (lihat
   ``eval/scoring/README.md``).
2. Untuk tiap kasus, panggil :func:`worker_light.scoring_client.score_job`
   — fungsi yang SAMA dengan yang dipakai produksi.
3. Cocokkan tiap segmen hasilnya dengan momen berlabel: HIT bila IoU >= 0,5.

Pemakaian::

    .venv-win\\Scripts\\python.exe scripts\\eval_scoring.py
    .venv-win\\Scripts\\python.exe scripts\\eval_scoring.py --json
    .venv-win\\Scripts\\python.exe scripts\\eval_scoring.py --case podcast-eps-12

Kode keluar: **0 selalu**, kecuali error nyata (kasus tidak ada, argumen salah).
Kualitas rendah BUKAN error. Peringatan "belum boleh diklaim" muncul bila
jumlah kasus berlabel < 20 — meniru aturan reframing (TECH_SPEC §5.2), supaya
peningkatan tidak diklaim dari noise.

Catatan: skrip ini memanggil penyedia AI sungguhan, jadi ia memakai API key
dan kuota yang sama dengan aplikasi. Jalankan hanya bila itu dapat diterima.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path

#: Akar repo: ``<repo>/scripts/eval_scoring.py``.
REPO_ROOT = Path(__file__).resolve().parents[1]

#: Jalur impor yang sama dengan launcher (scripts\\run-*.cmd), supaya skrip ini
#: bisa dijalankan langsung tanpa menyetel PYTHONPATH dari luar.
for _entry in ("apps/worker-render", "packages/shared/src", "apps/worker-light", "apps/api"):
    _path = str(REPO_ROOT / _entry)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from clipper_shared.scoring_eval import (  # noqa: E402
    MIN_CASES_FOR_CLAIM,
    CaseResult,
    DatasetReport,
    ScoringCase,
    aggregate,
    evaluate_case,
    load_dataset,
)
from worker_light import scoring_client  # noqa: E402

#: Direktori dataset.
DEFAULT_DATASET = REPO_ROOT / "eval" / "scoring" / "cases"

logger = logging.getLogger("eval_scoring")


def _resolve_provider() -> dict[str, object]:
    """Ambil konfigurasi penyedia dari lingkungan.

    Sengaja tidak membaca database aplikasi: eval harus bisa dijalankan dari
    shell bersih tanpa menyentuh ``output/clipper.db`` (aturan repositori).

    Returns:
        Kamus konfigurasi untuk ``score_job``.

    Raises:
        SystemExit: bila variabel lingkungan yang wajib belum diisi.
    """
    api_key = os.getenv("AI_API_KEY", "").strip()
    model = os.getenv("AI_MODEL", "").strip()
    if not api_key or not model:
        print(
            "AI_API_KEY dan AI_MODEL wajib diisi. Contoh:\n"
            "  set AI_API_KEY=...\n"
            "  set AI_MODEL=gemini-2.5-flash\n"
            "  .venv-win\\Scripts\\python.exe scripts\\eval_scoring.py",
            file=sys.stderr,
        )
        raise SystemExit(2)

    base_url = os.getenv(
        "AI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"
    ).strip()
    preset = os.getenv("AI_PRESET", "gemini").strip()
    allow_local = os.getenv("AI_ALLOW_LOCAL", "false").strip().lower() == "true"
    context_raw = os.getenv("AI_CONTEXT_TOKENS", "").strip()

    return {
        "preset": preset,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "allow_private": allow_local,
        "default_direction": "",
        "context_tokens": int(context_raw) if context_raw.isdigit() else None,
    }


def _words_from_case(case: ScoringCase) -> list[dict[str, object]]:
    """Ubah transkrip kasus menjadi daftar kata bertimestamp.

    Berkas kasus menyimpan transkrip dalam format jadi (``[12.4s] teks ...``)
    agar eval tidak bergantung pada kode rendering. Fungsi ini membaliknya
    menjadi daftar kata seperti keluaran STT, karena ``score_job`` menerima
    kata, bukan teks.

    Args:
        case: Kasus berlabel.

    Returns:
        Daftar kata dengan ``text``, ``start_s``, ``end_s``.
    """
    import re

    line_re = re.compile(r"^\[(\d+(?:\.\d+)?)s\]\s*(.*)$")
    words: list[dict[str, object]] = []
    for line in case.transcript.splitlines():
        match = line_re.match(line.strip())
        if not match:
            continue
        bucket_start = float(match.group(1))
        tokens = match.group(2).split()
        if not tokens:
            continue
        # Sebarkan kata merata di dalam bucket: posisi kata presisi tidak
        # dipakai metrik, tetapi batas akhir dipakai score_job untuk memotong
        # segmen liar, jadi rentang waktunya harus masuk akal.
        step = 1.0 / len(tokens)
        for index, token in enumerate(tokens):
            start = bucket_start + index * step
            words.append({"text": token, "start_s": start, "end_s": start + step})
    return words


def run_case(
    case: ScoringCase, provider: dict[str, object], *, repeats: int = 1
) -> CaseResult:
    """Jalankan satu kasus melalui jalur produksi.

    **Mengapa bisa dijalankan berkali-kali.** Keluarannya tidak deterministik:
    model yang sama pada masukan yang sama kadang memilih rentang berbeda,
    kadang mengembalikan kosong. Satu pemanggilan lalu memberi hasil HIT atau
    MISS tanpa ada perubahan kode — dan angka agregatnya ikut bergeser.

    Karena itu tiap kasus dijalankan ``repeats`` kali, dan yang dipakai adalah
    **keputusan mayoritas**. Ini bukan sekadar ukuran yang lebih baik: ia juga
    membuat ketidakstabilan itu terlihat lewat field ``stable``.

    Args:
        case: Kasus berlabel.
        provider: Konfigurasi penyedia.
        repeats: Berapa kali menjalankan tiap kasus. Nilai 1 mengembalikan
            perilaku lama (satu pemanggilan).

    Returns:
        :class:`CaseResult`; field ``error`` terisi bila gagal.
    """
    words = _words_from_case(case)
    if not words:
        return CaseResult(
            slug=case.slug,
            expected_count=len(case.expected),
            error="Transkrip kasus kosong atau format baris tidak dikenali.",
        )

    attempts = max(1, repeats)
    results: list[CaseResult] = []
    for _ in range(attempts):
        try:
            outcome = scoring_client.score_job(
                words=words,
                target_count=case.target_count,
                provider=provider,
                user_direction="",
                allow_private=bool(provider.get("allow_private")),
            )
        except Exception as exc:  # noqa: BLE001 - eval tidak boleh berhenti di satu kasus
            return CaseResult(
                slug=case.slug,
                expected_count=len(case.expected),
                error=f"{type(exc).__name__}: {exc}",
            )

        if outcome.error and not outcome.segments:
            return CaseResult(
                slug=case.slug, expected_count=len(case.expected), error=outcome.error
            )

        predicted = [(segment.start_s, segment.end_s) for segment in outcome.segments]
        scores = [float(segment.score or 0.0) for segment in outcome.segments]
        results.append(evaluate_case(case, predicted, scores=scores))

    if attempts == 1:
        return results[0]

    # Mayoritas: berapa kali tiap kesimpulan muncul, ambil yang paling sering.
    verdict_counts: dict[bool, int] = {}
    for result in results:
        hit = result.hits > 0
        verdict_counts[hit] = verdict_counts.get(hit, 0) + 1
    majority_hit, votes = max(verdict_counts.items(), key=lambda item: item[1])

    majority = results[0]
    if majority_hit != (majority.hits > 0):
        majority = next(result for result in results if (result.hits > 0) == majority_hit)

    return replace(
        majority,
        repeats=attempts,
        majority_votes=votes,
        stable=(len(verdict_counts) == 1),
    )


def _print_report(report: DatasetReport) -> None:
    """Cetak tabel per kasus + ringkasan ke stdout."""
    print()
    print(f"Dataset    : {DEFAULT_DATASET}")
    print(f"Ambang IoU : {report.iou_threshold:.2f}")
    print()
    header = f"{'kasus':<32} {'pred':>4} {'eks':>4} {'hit':>4} {'IoU':>6} {'presisi':>8} {'recall':>7} {'F1':>7}  stabil"
    print(header)
    print("-" * len(header))
    for case in report.cases:
        if case.error:
            print(f"{case.slug:<32} {'GAGAL: ' + case.error}")
            continue
        if case.repeats > 1:
            stabil = f"{case.majority_votes}/{case.repeats}" if case.stable else f"tidak ({case.majority_votes}/{case.repeats})"
        else:
            stabil = "-"
        print(
            f"{case.slug:<32} {case.predicted_count:>4} {case.expected_count:>4} "
            f"{case.hits:>4} {case.best_iou:>6.3f} {case.precision:>7.0%} {case.recall:>6.0%} {case.f1:>6.0%}  {stabil}"
        )

    print()
    print(f"Kasus berlabel       : {report.cases_total} (gagal: {report.cases_failed})")
    print(f"Segmen diajukan      : {report.predicted_total}")
    print(f"Momen berlabel       : {report.expected_total}")
    print(f"HIT                  : {report.hits_total}")
    print(f"IoU median           : {report.median_iou:.3f}")
    print(f"IoU terendah         : {report.min_iou:.3f}")
    print(f"Kasus IoU < 0.7      : {report.weak_cases_count} dari {report.cases_total}")
    print(f"Presisi              : {report.precision:.1%}")
    print(f"Recall               : {report.recall:.1%}")
    print(f"F1                   : {report.f1:.1%}")
    if report.unstable_cases:
        print()
        print(f"Kasus TIDAK STABIL   : {len(report.unstable_cases)} dari {report.cases_total}")
        for slug in report.unstable_cases:
            print(f"  - {slug}")

    print()
    if report.claimable:
        print(
            f"[ok] {report.cases_total} kasus >= {MIN_CASES_FOR_CLAIM}: angka ini sudah "
            "boleh diklaim (selama semua kasus benar-benar berlabel)."
        )
    else:
        print(
            f"!!! BELUM BOLEH DIKLAIM: baru {report.cases_total} kasus berlabel, "
            f"minimum {MIN_CASES_FOR_CLAIM}. !!!"
        )
        print("    Angka di atas hanya indikasi awal, bukan hasil pengukuran yang sah.")


def _report_to_json(report: DatasetReport) -> dict[str, object]:
    """Bentuk keluaran mesin (``--json``)."""
    return {
        "iou_threshold": report.iou_threshold,
        "cases_total": report.cases_total,
        "cases_failed": report.cases_failed,
        "predicted_total": report.predicted_total,
        "expected_total": report.expected_total,
        "hits_total": report.hits_total,
        "precision": round(report.precision, 4),
        "recall": round(report.recall, 4),
        "f1": round(report.f1, 4),
        "min_cases_for_claim": MIN_CASES_FOR_CLAIM,
        "claimable": report.claimable,
        "unstable_cases": list(report.unstable_cases),
        "cases": [
            {
                "slug": case.slug,
                "predicted_count": case.predicted_count,
                "expected_count": case.expected_count,
                "hits": case.hits,
                "precision": round(case.precision, 4),
                "recall": round(case.recall, 4),
                "f1": round(case.f1, 4),
                "repeats": case.repeats,
                "majority_votes": case.majority_votes,
                "stable": case.stable,
                "missed": list(case.missed),
                "error": case.error,
            }
            for case in report.cases
        ],
    }


def main(argv: list[str] | None = None) -> int:
    """Titik masuk CLI.

    Args:
        argv: Argumen baris perintah; ``None`` berarti memakai ``sys.argv``.

    Returns:
        Kode keluar proses.
    """
    parser = argparse.ArgumentParser(
        description="Ukur kualitas pemilihan momen pada dataset berlabel."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET, help="Direktori kasus.")
    parser.add_argument("--case", help="Jalankan satu kasus saja (berdasar slug).")
    parser.add_argument("--json", action="store_true", help="Keluaran mesin.")
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="Berapa kali tiap kasus dijalankan; hasil mayoritas yang dipakai.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    if not args.dataset.is_dir():
        print(f"Direktori dataset tidak ada: {args.dataset}", file=sys.stderr)
        return 2

    cases = load_dataset(args.dataset)
    if args.case:
        cases = [case for case in cases if case.slug == args.case]
    if not cases:
        print(
            f"Tidak ada kasus berlabel di {args.dataset}.\n"
            "Buat berkas *.json di sana (lihat eval/scoring/README.md), atau bootstrapping "
            "dari job yang sudah ada dengan scripts/bootstrap_eval_cases.py.",
            file=sys.stderr,
        )
        return 2

    provider = _resolve_provider()
    results = [run_case(case, provider, repeats=args.repeats) for case in cases]
    report = aggregate(results)

    if args.json:
        print(json.dumps(_report_to_json(report), indent=2, ensure_ascii=False))
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
