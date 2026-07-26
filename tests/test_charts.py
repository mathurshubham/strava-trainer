"""Smoke tests for all 22 chart presets — PRD §11.1: 'All 22 charts render'
and Phase 5 exit criteria: 'Every preset renders from real data without
exception.' These check the renderer doesn't raise and produces a valid PNG,
not pixel-perfect output.
"""
from datetime import date, datetime, timedelta

import pytest

from app.charts import cross, endurance, strength
from app.models.enums import SetType
from app.models.streams import StreamSample
from app.models.workout import MuscleGroupVolume, SetRecord, SplitRecord, ZoneTime

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _assert_png(buf) -> None:
    data = buf.read()
    assert data.startswith(PNG_MAGIC)
    assert len(data) > 500


# ── strength ──────────────────────────────────────────────────────────────


def test_s01_volume_by_muscle():
    _assert_png(strength.s01_volume_by_muscle([
        MuscleGroupVolume(muscle_group="chest", volume_kg=1320, working_sets=3),
        MuscleGroupVolume(muscle_group="back", volume_kg=1000, working_sets=2),
    ]))


def test_s02_volume_trend():
    sessions = [(datetime(2026, 7, d), 1000 + d * 10) for d in range(1, 20)]
    _assert_png(strength.s02_volume_trend(sessions))


def test_s03_e1rm_trend():
    points = [(datetime(2026, 7, d), 70 + d) for d in range(1, 20)]
    _assert_png(strength.s03_e1rm_trend("Bench Press", points))


def test_s04_set_breakdown():
    sets = [
        SetRecord(set_index=0, weight_kg=40, reps=10, set_type=SetType.WARMUP),
        SetRecord(set_index=1, weight_kg=60, reps=8, set_type=SetType.NORMAL),
        SetRecord(set_index=2, weight_kg=60, reps=6, set_type=SetType.NORMAL),
    ]
    _assert_png(strength.s04_set_breakdown("Bench Press", sets))


def test_s05_session_vs_last():
    current = {"Bench Press": 1320, "Barbell Row": 1000}
    previous = {"Bench Press": 1080, "Barbell Row": 900}
    _assert_png(strength.s05_session_vs_last(current, previous))


def test_s06_weekly_sets_vs_target():
    _assert_png(strength.s06_weekly_sets_vs_target({"chest": 14, "back": 8, "legs": 22}))


def test_s07_rep_range_mix():
    _assert_png(strength.s07_rep_range_mix({"strength": 6, "hypertrophy": 12, "endurance": 2}))


def test_s08_pr_timeline():
    prs = [(datetime(2026, 7, d), "Bench Press", 70 + d) for d in (1, 8, 15)]
    _assert_png(strength.s08_pr_timeline(prs))


def test_s09_load_reps_scatter():
    sets = [(60, 8), (60, 6), (65, 5)]
    _assert_png(strength.s09_load_reps_scatter(sets, "Bench Press"))


def test_s10_muscle_balance_radar():
    _assert_png(strength.s10_muscle_balance_radar({"push": 2640, "pull": 1500, "legs": 0, "core": 300}))


def test_s11_intensity_distribution():
    _assert_png(strength.s11_intensity_distribution([60, 70, 80, 90, 65, 75]))


def test_s12_frequency_heatmap():
    dates = [date(2026, 7, 1) + timedelta(days=i * 3) for i in range(15)]
    _assert_png(strength.s12_frequency_heatmap(dates))


# ── endurance ─────────────────────────────────────────────────────────────


def test_e01_hr_zone_distribution():
    zones = [
        ZoneTime(zone="Z1 Recovery", seconds=600, pct_of_moving_time=40),
        ZoneTime(zone="Z2 Aerobic", seconds=900, pct_of_moving_time=60),
    ]
    _assert_png(endurance.e01_hr_zone_distribution(zones))


def _sample_stream(n=60):
    return [
        StreamSample(time_s=i * 10, heartrate=140 + (i % 10), distance_m=i * 30, altitude_m=10 + i, grade_pct=1.0, moving=True)
        for i in range(n)
    ]


def test_e02_hr_over_time_zoned():
    boundaries = [("Z1 Recovery", 95, 114), ("Z2 Aerobic", 114, 133), ("Z3 Tempo", 133, 152)]
    _assert_png(endurance.e02_hr_over_time_zoned(_sample_stream(), boundaries))


def test_e03_pace_hr_dual():
    _assert_png(endurance.e03_pace_hr_dual(_sample_stream()))


def test_e04_splits_bar():
    splits = [SplitRecord(split_index=i, distance_m=1000, time_s=300 + i * 5, pace_s_per_km=300 + i * 5) for i in range(5)]
    _assert_png(endurance.e04_splits_bar(splits))


def test_e05_decoupling():
    _assert_png(endurance.e05_decoupling(0.857, 0.8))


def test_e06_run_overlay():
    series_a = [(d, 300 + d) for d in range(10)]
    series_b = [(d, 310 + d) for d in range(10)]
    _assert_png(endurance.e06_run_overlay(series_a, series_b, "Today", "Last Tuesday", "Pace (s/km)"))


def test_e07_weekly_volume_zones():
    weekly = {
        "Wk1": {"Z1 Recovery": 20, "Z2 Aerobic": 10},
        "Wk2": {"Z1 Recovery": 15, "Z3 Tempo": 8},
    }
    _assert_png(endurance.e07_weekly_volume_zones(weekly))


def test_e08_zone_mix_28d():
    _assert_png(endurance.e08_zone_mix_28d(78, 22))


def test_e09_cadence_over_time():
    samples = [(i * 10, 175 + (i % 5)) for i in range(60)]
    _assert_png(endurance.e09_cadence_over_time(samples))


def test_e10_elevation_hr():
    _assert_png(endurance.e10_elevation_hr(_sample_stream()))


# ── cross ─────────────────────────────────────────────────────────────────


def test_x01_training_load_acwr():
    dates = [date(2026, 7, 1) + timedelta(weeks=i) for i in range(8)]
    acute = [(d, 300 + i * 10) for i, d in enumerate(dates)]
    chronic = [(d, 1000 + i * 20) for i, d in enumerate(dates)]
    _assert_png(cross.x01_training_load_acwr(acute, chronic))


def test_x02_consistency_calendar():
    activities = [(date(2026, 7, 1) + timedelta(days=i * 2), "run" if i % 2 else "weighttraining") for i in range(20)]
    _assert_png(cross.x02_consistency_calendar(activities))
