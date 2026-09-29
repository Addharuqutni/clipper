"""Ukur presisi reframing reframer pada dataset klip berlabel.

Target PRD "presisi reframing >= 85%" adalah **target, belum hasil** (TECH_SPEC
§5.2; Sprint 3 butir 31: *"Ukur dulu pada 20 klip berlabel, baru klaim
angkanya"*). Skrip ini yang mengukurnya:

1. Membaca ``<dataset>/labels.json`` (lihat ``eval/reframe/README.md``).
2. Untuk tiap klip, memanggil :func:`worker_render.reframer.compute_crop_positions`
   — fungsi yang SAMA dengan yang dipakai render sungguhan, tetapi berhenti
   sebelum tahap encode, jadi tidak ada berkas video yang ditulis.
3. Menilai tiap keyframe: HIT bila titik tengah wajah yang dilabeli berada di
   dalam jendela crop 9:16 pada frame itu (dan HIT bermargin bila berada di
   dalam 80% tengah jendela).

Pemakaian::

    .venv-win\\Scripts\\python.exe scripts\\eval_reframe.py
    .venv-win\\Scripts\\python.exe scripts\\eval_reframe.py --json
    .venv-win\\Scripts\\python.exe scripts\\eval_reframe.py --margin 0.1

Kode keluar: **0 selalu**, kecuali error nyata (label tidak ada, argumen salah,
klip gagal dibaca) — presisi rendah BUKAN error. Peringatan "belum boleh
diklaim" muncul bila jumlah klip berlabel < 20.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

#: Akar repo: ``<repo>/scripts/eval_reframe.py``.
REPO_ROOT = Path(__file__).resolve().parents[1]

#: Jalur impor yang sama dengan launcher (scripts\\run-*.cmd), supaya skrip ini
#: bisa dijalankan langsung tanpa menyetel PYTHONPATH dari luar.
for _entry in ("apps/worker-render", "packages/shared/src", "apps/worker-light", "apps/api"):
    _path = str(REPO_ROOT / _entry)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from clipper_shared.reframe import crop_width_for  # noqa: E402
from worker_render.reframe_eval import (  # noqa: E402
    CLIPS_DIRNAME,
    DEFAULT_MARGIN_FRAC,
    MIN_CLIPS_FOR_CLAIM,
    TARGET_PRECISION,
    ClipEvaluation,
    ClipLabel,
    DatasetReport,
    evaluate_clip,
    is_out_of_range,
    load_labels,
)
from worker_render.reframer import (  # noqa: E402
    SourceInfo,
    compute_crop_positions,
    default_model_path,
    probe_source,
)

logger = logging.getLogger("eval_reframe")

#: Direktori dataset bawaan, relatif ke akar repo.
DEFAULT_DATASET = REPO_ROOT / "eval" / "reframe"


@dataclass(frozen=True, slots=True)
class ClipFailure:
    """Klip yang gagal diproses (berkas hilang, ffprobe menolak, dst.)."""

    clip: str
    reason: str


def _load_env() -> None:
    """Muat ``.env`` (FFMPEG_BINARY, FACE_LANDMARKER_MODEL) bila dotenv tersedia.

    Tanpa ini, jalur FFmpeg dan model wajah jatuh ke bawaan
    :func:`clipper_shared.storage.binary`/:func:`default_model_path`, yang bisa
    berbeda dari yang dipakai server.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dotenv ikut dependensi `app`.
        logger.debug("python-dotenv tidak terpasang; memakai env proses apa adanya.")
        return
    load_dotenv(REPO_ROOT / ".env")


def _resolve_clip_path(dataset_dir: Path, clip: str) -> Path:
    """Temukan berkas klip: jalur absolut, relatif dataset, atau di ``clips/``."""
    candidate = Path(clip)
    if candidate.is_absolute():
        return candidate
    relative = dataset_dir / candidate
    if relative.is_file():
        return relative
    return dataset_dir / CLIPS_DIRNAME / candidate


def evaluate_dataset(
    dataset_dir: Path,
    labels: tuple[ClipLabel, ...],
    *,
    margin_frac: float,
    model_path: str,
) -> tuple[list[ClipEvaluation], list[ClipFailure]]:
    """Jalankan reframer per klip dan nilai keyframe-nya.

    Pelacakan wajah dijalankan untuk SELURUH durasi klip (0..durasi), sama
    seperti render sungguhan yang memakai ``start_s`` = awal klip berlabel.
    Kemajuan ditulis ke stderr supaya stdout tetap bersih untuk ``--json``.
    """
    evaluations: list[ClipEvaluation] = []
    failures: list[ClipFailure] = []

    for index, label in enumerate(labels, start=1):
        clip_path = _resolve_clip_path(dataset_dir, label.clip)
        print(f"[{index}/{len(labels)}] {label.clip} ...", file=sys.stderr, flush=True)
        if not clip_path.is_file():
            failures.append(ClipFailure(clip=label.clip, reason=f"berkas tidak ditemukan: {clip_path}"))
            continue

        try:
            info: SourceInfo = probe_source(clip_path)
        except (RuntimeError, OSError) as exc:
            failures.append(ClipFailure(clip=label.clip, reason=f"ffprobe gagal: {exc}"))
            continue

        positions, health = compute_crop_positions(
            clip_path,
            info,
            start_s=0.0,
            end_s=info.duration_s,
            face_model_path=model_path,
        )
        crop_w = crop_width_for(info.width, info.height)
        evaluation = evaluate_clip(
            label,
            positions=positions,
            fps=info.fps,
            source_width=info.width,
            source_height=info.height,
            crop_w=crop_w,
            duration_s=info.duration_s,
            tracking_health=health,
            margin_frac=margin_frac,
        )

        # Keyframe di luar durasi/lintasan dihitung MISS oleh evaluate_clip.
        # Dilaporkan agar label yang salah waktu cepat terlihat, bukan seolah
        # pelacakan yang gagal.
        for score in evaluation.scores:
            if is_out_of_range(score.t, fps=info.fps, frame_count=len(positions), duration_s=info.duration_s):
                print(
                    f"    ! keyframe t={score.t:.3f}s melewati durasi klip "
                    f"({info.duration_s:.3f}s) — dihitung MISS.",
                    file=sys.stderr,
                )

        evaluations.append(evaluation)

    return evaluations, failures


def _print_report(report: DatasetReport, failures: list[ClipFailure]) -> None:
    """Cetak tabel per klip + ringkasan ke stdout."""
    print()
    print(f"Dataset   : {report.dataset}")
    print(
        f"Margin    : {report.margin_frac:.0%} ({report.margin_frac / 2:.0%} dibuang dari tiap tepi; "
        f"HIT bermargin = wajah di dalam {(1 - report.margin_frac):.0%} tengah crop)"
    )
    print()
    header = f"{'klip':<28} {'kf':>3} {'hit':>3} {'hit80%':>6} {'presisi':>8} {'presisi80%':>10}  pelacakan"
    print(header)
    print("-" * len(header))
    for clip in report.clips:
        health = clip.tracking_health or ("TIDAK ADA WAJAH" if clip.no_face else "")
        print(
            f"{clip.clip:<28} {clip.keyframes_total:>3} {clip.hits:>3} "
            f"{clip.margin_hits:>6} {clip.precision:>7.0%} {clip.margin_precision:>9.0%}  {health}"
        )

    if failures:
        print()
        print("Klip GAGAL diproses (tidak masuk hitungan):", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure.clip}: {failure.reason}", file=sys.stderr)

    print()
    print(f"Klip berlabel        : {report.clips_total}")
    print(f"Keyframe dinilai     : {report.keyframes_total}")
    print(f"HIT                  : {report.hits_total} ({report.precision:.1%})")
    print(
        f"HIT bermargin ({1 - report.margin_frac:.0%})   : "
        f"{report.margin_hits_total} ({report.margin_precision:.1%})"
    )
    print(f"Klip tanpa wajah     : {report.clips_without_face}")
    print(f"Target PRD           : {TARGET_PRECISION:.0%}")
    if report.keyframes_total:
        verdict = "TERCAPAI" if report.meets_target else "BELUM TERCAPAI"
        print(f"Status vs target     : {verdict} (presisi {report.precision:.1%})")
    else:
        print("Status               : tidak ada keyframe yang bisa dinilai.")

    print()
    if report.claimable:
        print(
            f"[ok] {report.clips_total} klip >= {MIN_CLIPS_FOR_CLAIM}: angka presisi ini "
            "sudah boleh diklaim (selama semua klip benar-benar berlabel)."
        )
    else:
        print(
            f"!!! BELUM BOLEH DIKLAIM: baru {report.clips_total} klip berlabel, "
            f"minimum {MIN_CLIPS_FOR_CLAIM} (TECH_SPEC §5.2, Sprint 3 butir 31). !!!"
        )
        print("    Angka di atas hanya indikasi awal, bukan hasil pengukuran yang sah.")


def _report_to_json(report: DatasetReport, failures: list[ClipFailure]) -> dict[str, object]:
    """Bentuk keluaran mesin (``--json``)."""
    return {
        "dataset": report.dataset,
        "margin_frac": report.margin_frac,
        "clips_total": report.clips_total,
        "keyframes_total": report.keyframes_total,
        "hits_total": report.hits_total,
        "margin_hits_total": report.margin_hits_total,
        "precision": round(report.precision, 4),
        "margin_precision": round(report.margin_precision, 4),
        "clips_without_face": report.clips_without_face,
        "clips_failed": [{"clip": failure.clip, "reason": failure.reason} for failure in failures],
        "target_precision": TARGET_PRECISION,
        "min_clips_for_claim": MIN_CLIPS_FOR_CLAIM,
        "claimable": report.claimable,
        "meets_target": report.meets_target,
        "clips": [
            {
                "clip": clip.clip,
                "source_width": clip.source_width,
                "source_height": clip.source_height,
                "fps": round(clip.fps, 4),
                "crop_w": clip.crop_w,
                "no_face": clip.no_face,
                "tracking_health": clip.tracking_health,
                "keyframes_total": clip.keyframes_total,
                "hits": clip.hits,
                "margin_hits": clip.margin_hits,
                "precision": round(clip.precision, 4),
                "margin_precision": round(clip.margin_precision, 4),
                "keyframes": [
                    {
                        "t": score.t,
                        "cx": score.cx,
                        "hit": score.hit,
                        "margin_hit": score.margin_hit,
                        "crop_x": score.crop_x,
                        "crop_w": score.crop_w,
                    }
                    for score in clip.scores
                ],
            }
            for clip in report.clips
        ],
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Argumen baris perintah."""
    parser = argparse.ArgumentParser(
        prog="eval_reframe.py",
        description="Ukur presisi reframing pada dataset klip berlabel (eval/reframe).",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help=f"direktori dataset berisi labels.json (bawaan: {DEFAULT_DATASET})",
    )
    margin_help = (
        "lebar crop (0..1) yang dibuang untuk HIT bermargin, dibagi rata ke kedua tepi "
        f"(bawaan: {DEFAULT_MARGIN_FRAC} = {DEFAULT_MARGIN_FRAC / 2:.0%} per tepi, "
        f"wajah di dalam {(1 - DEFAULT_MARGIN_FRAC):.0%} tengah crop)"
    )
    parser.add_argument(
        "--margin",
        type=float,
        default=DEFAULT_MARGIN_FRAC,
        # argparse memperlakukan "%" pada teks help sebagai format string.
        help=margin_help.replace("%", "%%"),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="keluarkan hasil sebagai JSON (stdout bersih; log ke stderr)",
    )
    parser.add_argument(
        "--model",
        default="",
        help="jalur model Face Landmarker (bawaan: FACE_LANDMARKER_MODEL atau .models/)",
    )
    parser.add_argument(
        "--clips",
        nargs="*",
        default=[],
        help="batasi ke nama berkas tertentu, mis. --clips a.mp4 b.mp4",
    )
    parser.add_argument("--verbose", action="store_true", help="tampilkan log DEBUG reframer")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Titik masuk. Kode keluar 1 hanya untuk error nyata."""
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if not 0.0 <= args.margin < 1.0:
        print(f"ERROR: --margin harus pada 0..1, bukan {args.margin}.", file=sys.stderr)
        return 1

    _load_env()
    dataset_dir: Path = args.dataset
    try:
        labels = load_labels(dataset_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.clips:
        wanted = set(args.clips)
        labels = tuple(label for label in labels if label.clip in wanted)
        missing = wanted - {label.clip for label in labels}
        if missing:
            print(f"ERROR: label untuk {sorted(missing)} tidak ada di labels.json.", file=sys.stderr)
            return 1
    if not labels:
        print(
            f"ERROR: {dataset_dir / 'labels.json'} tidak memuat klip yang bisa dinilai.",
            file=sys.stderr,
        )
        return 1

    evaluations, failures = evaluate_dataset(
        dataset_dir,
        labels,
        margin_frac=args.margin,
        model_path=args.model or default_model_path(),
    )
    report = DatasetReport(
        dataset=str(dataset_dir),
        margin_frac=args.margin,
        clips=tuple(evaluations),
    )

    if args.json:
        json.dump(_report_to_json(report, failures), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        _print_report(report, failures)

    # Kegagalan per klip tetap "error" menurut definisi skrip ini: hasilnya tidak
    # lengkap, jadi jangan biarkan pemanggil otomatis menganggapnya sah.
    return 1 if failures else 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main())
