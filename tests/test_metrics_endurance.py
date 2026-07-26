"""Endurance metrics — especially the Δt-weighted zone math the PRD calls out
as the thing most likely to silently corrupt results if done wrong (§2.3, §11.1:
"Zone maths correct on an irregularly-sampled stream fixture").
"""
import math

import pytest

from app.metrics.endurance import (
    build_load_context,
    cadence_spm,
    classify_run,
    compute_acwr,
    compute_decoupling_pct,
    compute_time_in_zone,
    compute_trimp,
    acwr_flag,
    efficiency_factor,
    pace_variability_cv,
    resolve_zone_boundaries,
)
from app.models.enums import RunClassification
from app.models.streams import LapRecord, StreamSample


def test_zone_resolution_priority_lthr_then_maxhr_then_estimate():
    _, source_lthr, _ = resolve_zone_boundaries(max_hr=190, lthr=170)
    assert source_lthr == "lthr"

    _, source_maxhr, _ = resolve_zone_boundaries(max_hr=190, lthr=None)
    assert source_maxhr == "max_hr"

    _, source_est, warnings = resolve_zone_boundaries(max_hr=None, lthr=None)
    assert source_est == "estimate"
    assert warnings  # must warn that this is an approximation


def test_time_in_zone_uses_delta_t_not_sample_count():
    """The core regression test: a 60-second gap at one HR value must count as
    60 seconds in that zone, not '1 sample out of 6' (~17%) the way naive
    sample-counting would get it wrong."""
    boundaries, _, _ = resolve_zone_boundaries(max_hr=190, lthr=None)
    # Z1 95-114, Z4 152-171 at max_hr=190

    samples = [
        StreamSample(time_s=0, heartrate=100, moving=True),
        StreamSample(time_s=1, heartrate=100, moving=True),
        StreamSample(time_s=2, heartrate=100, moving=True),
        StreamSample(time_s=3, heartrate=160, moving=True),   # long gap starts here
        StreamSample(time_s=63, heartrate=160, moving=True),  # 60s gap at Z4 HR
        StreamSample(time_s=64, heartrate=100, moving=True),
        StreamSample(time_s=65, heartrate=100, moving=True),
    ]
    zones = compute_time_in_zone(samples, boundaries)
    by_name = {z.zone: z.seconds for z in zones}

    assert by_name["Z1 Recovery"] == pytest.approx(4.0)
    assert by_name["Z4 Threshold"] == pytest.approx(61.0)  # NOT ~17% from naive sample counting

    # PRD §11.1: "HR zone times sum to total moving time (±2s tolerance)"
    total_span = samples[-1].time_s - samples[0].time_s
    assert sum(by_name.values()) == pytest.approx(total_span, abs=2)


def test_time_in_zone_excludes_non_moving_intervals():
    boundaries, _, _ = resolve_zone_boundaries(max_hr=190, lthr=None)
    samples = [
        StreamSample(time_s=0, heartrate=100, moving=True),
        StreamSample(time_s=10, heartrate=100, moving=False),  # paused for the next 10s
        StreamSample(time_s=20, heartrate=190, moving=True),
        StreamSample(time_s=30, heartrate=190, moving=True),
    ]
    zones = compute_time_in_zone(samples, boundaries)
    by_name = {z.zone: z.seconds for z in zones}
    assert by_name["Z1 Recovery"] == pytest.approx(10.0)
    assert by_name["Z5 VO2max"] == pytest.approx(10.0)
    assert sum(by_name.values()) == pytest.approx(20.0)  # the paused 10s excluded entirely


def test_efficiency_factor():
    assert efficiency_factor(10_000, 3000, 150) == pytest.approx(1.3333, abs=0.001)
    assert efficiency_factor(10_000, 3000, None) is None


def test_decoupling_detects_cardiac_drift():
    samples = []
    for t in range(0, 601, 100):
        samples.append(StreamSample(time_s=t, heartrate=140, distance_m=2 * t, moving=True))
    for t in range(700, 1201, 100):
        samples.append(StreamSample(time_s=t, heartrate=150, distance_m=2 * t, moving=True))

    decoupling = compute_decoupling_pct(samples)
    assert decoupling == pytest.approx(6.6667, abs=0.01)


def test_trimp_matches_banister_formula():
    duration_min, avg_hr, resting_hr, max_hr = 60, 150, 50, 190
    hrr = (avg_hr - resting_hr) / (max_hr - resting_hr)
    expected = duration_min * hrr * 0.64 * math.exp(1.92 * hrr)
    assert compute_trimp(duration_min, avg_hr, resting_hr, max_hr) == pytest.approx(expected)
    assert compute_trimp(60, None, 50, 190) is None


def test_acwr_sweet_spot_and_risk_flags():
    assert compute_acwr(250, 1000) == pytest.approx(1.0)
    assert acwr_flag(compute_acwr(250, 1000)) == "sweet_spot"
    assert acwr_flag(compute_acwr(500, 1000)) == "elevated_risk"  # 2.0
    assert acwr_flag(compute_acwr(350, 1000)) == "outside_sweet_spot"  # 1.4


def test_cadence_doubles_per_leg_value():
    assert cadence_spm(85) == 170


def test_pace_variability_cv():
    assert pace_variability_cv([300, 300, 300]) == pytest.approx(0.0)
    assert pace_variability_cv([280, 300, 320]) == pytest.approx(20 / 300, abs=0.001)


def test_classify_run_race_by_workout_type():
    result, confidence, evidence = classify_run(
        moving_time_s=1800, distance_m=5000, zone_pcts={}, longest_z4plus_block_s=0,
        longest_z3_block_s=0, laps=[], split_paces_s_per_km=[], median_28d_distance_m=None,
        workout_type=1, is_best_effort_pr=False,
    )
    assert result == RunClassification.RACE
    assert confidence >= 0.9


def test_classify_run_easy_recovery():
    result, _, evidence = classify_run(
        moving_time_s=2400, distance_m=6000,
        zone_pcts={"Z1 Recovery": 50, "Z2 Aerobic": 30, "Z3 Tempo": 20},
        longest_z4plus_block_s=0, longest_z3_block_s=200, laps=[], split_paces_s_per_km=[],
        median_28d_distance_m=None, workout_type=None, is_best_effort_pr=False,
    )
    assert result == RunClassification.EASY_RECOVERY
    assert evidence


def test_classify_run_intervals_from_lap_clustering():
    laps = [
        LapRecord(lap_index=i, distance_m=1000, moving_time_s=t, average_heartrate=150)
        for i, t in enumerate([300, 60, 300, 60, 300, 60])
    ]
    result, _, evidence = classify_run(
        moving_time_s=1080, distance_m=6000, zone_pcts={}, longest_z4plus_block_s=0,
        longest_z3_block_s=0, laps=laps, split_paces_s_per_km=[], median_28d_distance_m=None,
        workout_type=None, is_best_effort_pr=False,
    )
    assert result == RunClassification.INTERVALS
    assert evidence


def test_classify_run_threshold_by_continuous_z4_block():
    result, _, _ = classify_run(
        moving_time_s=2000, distance_m=6000, zone_pcts={"Z4 Threshold": 60},
        longest_z4plus_block_s=1500, longest_z3_block_s=0, laps=[], split_paces_s_per_km=[],
        median_28d_distance_m=None, workout_type=None, is_best_effort_pr=False,
    )
    assert result == RunClassification.THRESHOLD


def test_classify_run_progression_negative_split():
    splits = [310, 309, 308, 300, 299, 298, 290, 289, 288]
    result, _, _ = classify_run(
        moving_time_s=2000, distance_m=6000, zone_pcts={"Z2 Aerobic": 40},
        longest_z4plus_block_s=0, longest_z3_block_s=0, laps=[], split_paces_s_per_km=splits,
        median_28d_distance_m=None, workout_type=None, is_best_effort_pr=False,
    )
    assert result == RunClassification.PROGRESSION


def test_classify_run_unclassified_when_nothing_matches():
    result, confidence, _ = classify_run(
        moving_time_s=1200, distance_m=3000, zone_pcts={"Z3 Tempo": 50, "Z2 Aerobic": 50},
        longest_z4plus_block_s=0, longest_z3_block_s=100, laps=[], split_paces_s_per_km=[],
        median_28d_distance_m=None, workout_type=None, is_best_effort_pr=False,
    )
    assert result == RunClassification.UNCLASSIFIED
    assert confidence < 0.5


def test_load_context_assembly():
    ctx = build_load_context(acute_7d_load=350, chronic_28d_load=1000, weekly_distance_km=40, zone1_2_pct_28d=78)
    assert ctx.acwr == pytest.approx(1.4)
    assert ctx.zone3_plus_pct_28d == pytest.approx(22)
