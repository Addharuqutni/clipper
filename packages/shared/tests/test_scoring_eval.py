"""Uji metrik pemilihan momen (:mod:`clipper_shared.scoring_eval`).

Bagian yang diuji di sini sengaja yang **paling mudah salah**: definisi HIT
(batas ambang), pemakaian satu momen hanya sekali, dan agregasi per total
segmen. Semua uji murni — tidak ada pemanggilan LLM.
"""

from __future__ import annotations

from clipper_shared.scoring_eval import (
    IOU_THRESHOLD,
    MIN_CASES_FOR_CLAIM,
    DatasetReport,
    ExpectedMoment,
    ScoringCase,
    aggregate,
    evaluate_case,
    intersection_over_union,
    load_dataset,
)


def _case(*moments: tuple[float, float], slug: str = "k") -> ScoringCase:
    return ScoringCase(
        slug=slug,
        transcript="[0.0s] halo",
        expected=tuple(ExpectedMoment(start_s=s, end_s=e) for s, e in moments),
    )


def test_iou_identical_is_one() -> None:
    assert intersection_over_union((10.0, 50.0), (10.0, 50.0)) == 1.0


def test_iou_disjoint_is_zero() -> None:
    assert intersection_over_union((0.0, 10.0), (20.0, 30.0)) == 0.0


def test_iou_half_overlap() -> None:
    # Rentang sama panjang, bergeser setengah: irisan 20, gabungan 60.
    assert intersection_over_union((0.0, 40.0), (20.0, 60.0)) == 20.0 / 60.0


def test_iou_zero_length_range_is_zero() -> None:
    assert intersection_over_union((5.0, 5.0), (5.0, 5.0)) == 0.0


def test_exact_match_counts_as_hit() -> None:
    result = evaluate_case(_case((100.0, 140.0)), [(100.0, 140.0)])
    assert result.hits == 1
    assert result.matched == (0,)
    assert result.spurious == ()


def test_prediction_missed_by_more_than_threshold() -> None:
    # Geser 30 detik dari rentang 40 detik: IoU = 10/70, di bawah ambang.
    result = evaluate_case(_case((100.0, 140.0)), [(130.0, 170.0)])
    assert result.hits == 0
    assert result.missed == (0,)
    assert result.spurious == ((130.0, 170.0),)


def test_boundary_iou_exactly_at_threshold_is_hit() -> None:
    # Label (0,60) vs prediksi (30,60): irisan 30, gabungan 60 -> IoU tepat
    # 0.5, dan ambang bersifat inklusif.
    case = _case((0.0, 60.0))
    result = evaluate_case(case, [(30.0, 60.0)], iou_threshold=IOU_THRESHOLD)
    assert intersection_over_union((0.0, 60.0), (30.0, 60.0)) == 0.5
    assert result.hits == 1


def test_iou_just_below_threshold_is_miss() -> None:
    # Label (0,60) vs prediksi (31,60): irisan 29, gabungan 60 -> 0.483.
    case = _case((0.0, 60.0))
    result = evaluate_case(case, [(31.0, 60.0)], iou_threshold=IOU_THRESHOLD)
    assert abs(intersection_over_union((0.0, 60.0), (31.0, 60.0)) - 29.0 / 60.0) < 1e-9
    assert result.hits == 0


def test_one_moment_used_only_once() -> None:
    # Dua prediksi bagus memperebutkan satu momen: hanya satu yang HIT.
    result = evaluate_case(_case((100.0, 140.0)), [(100.0, 140.0), (101.0, 141.0)])
    assert result.hits == 1
    assert result.predicted_count == 2
    assert result.precision == 0.5


def test_higher_score_wins_contested_moment() -> None:
    # Prediksi lemah (skor 10) menang urutan masuk, tetapi skor tinggi (90)
    # yang berhak atas momen itu; prediksi lemah jadi spurious.
    case = _case((100.0, 140.0))
    result = evaluate_case(
        case, [(130.0, 170.0), (100.0, 140.0)], scores=[10.0, 90.0]
    )
    assert result.hits == 1
    assert result.spurious == ((130.0, 170.0),)


def test_recall_counts_missed_moments() -> None:
    result = evaluate_case(_case((0.0, 40.0), (200.0, 240.0)), [(0.0, 40.0)])
    assert result.hits == 1
    assert result.recall == 0.5
    assert result.missed == (1,)


def test_no_predictions_gives_zero_precision() -> None:
    result = evaluate_case(_case((0.0, 40.0)), [])
    assert result.precision == 0.0
    assert result.recall == 0.0
    assert result.f1 == 0.0


def test_f1_is_harmonic_mean() -> None:
    result = evaluate_case(_case((0.0, 40.0), (200.0, 240.0)), [(0.0, 40.0)])
    assert result.precision == 1.0
    assert result.recall == 0.5
    assert abs(result.f1 - (2 * 1.0 * 0.5 / 1.5)) < 1e-9


def test_aggregate_pools_by_total_segments_not_per_case() -> None:
    # Kasus A: 1 dari 1 benar. Kasus B: 1 dari 3 benar.
    # Rata-rata per kasus = (1.0 + 0.333)/2 = 0.667, tetapi pooling per
    # segmen = 2/4 = 0.5. Agregasi yang benar menurut repo adalah pooling.
    report = aggregate(
        [
            evaluate_case(_case((0.0, 40.0), slug="a"), [(0.0, 40.0)]),
            evaluate_case(
                _case((0.0, 40.0), (100.0, 140.0), (200.0, 240.0), slug="b"),
                [(0.0, 40.0), (300.0, 340.0), (400.0, 440.0)],
            ),
        ]
    )
    assert report.predicted_total == 4
    assert report.hits_total == 2
    assert report.precision == 0.5


def test_failed_case_excluded_from_totals() -> None:
    from clipper_shared.scoring_eval import CaseResult

    report = aggregate(
        [
            CaseResult(slug="ok", predicted_count=2, expected_count=2, hits=2),
            CaseResult(slug="broken", expected_count=2, error="boom"),
        ]
    )
    assert report.cases_total == 2
    assert report.cases_failed == 1
    assert report.predicted_total == 2
    assert report.hits_total == 2
    assert report.precision == 1.0


def test_claimable_only_at_minimum_cases() -> None:
    from clipper_shared.scoring_eval import CaseResult

    few = aggregate([CaseResult(slug=f"c{i}") for i in range(MIN_CASES_FOR_CLAIM - 1)])
    assert not few.claimable

    enough = aggregate([CaseResult(slug=f"c{i}") for i in range(MIN_CASES_FOR_CLAIM)])
    assert enough.claimable


def test_invalid_moment_range_rejected() -> None:
    assert not ExpectedMoment(start_s=50.0, end_s=40.0).is_valid()
    assert not ExpectedMoment(start_s=-1.0, end_s=40.0).is_valid()
    assert ExpectedMoment(start_s=0.0, end_s=40.0).is_valid()


def test_load_dataset_skips_example_and_missing_dir(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert load_dataset(tmp_path / "nope") == []

    (tmp_path / "a.json").write_text(
        '{"slug":"a","transcript":"[0.0s] halo","expected":[{"start_s":0,"end_s":40}]}',
        encoding="utf-8",
    )
    (tmp_path / "b.json.example").write_text('{"slug":"b"}', encoding="utf-8")
    cases = load_dataset(tmp_path)
    assert [case.slug for case in cases] == ["a"]
    assert cases[0].expected[0].end_s == 40.0


def test_load_case_rejects_empty_transcript(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import pytest

    (tmp_path / "bad.json").write_text('{"slug":"bad","transcript":"  "}', encoding="utf-8")
    with pytest.raises(ValueError, match="transcript"):
        from clipper_shared.scoring_eval import load_case

        load_case(tmp_path / "bad.json")


def test_report_defaults_are_zero() -> None:
    report = DatasetReport()
    assert report.precision == 0.0
    assert report.recall == 0.0
    assert report.f1 == 0.0
    assert not report.claimable
