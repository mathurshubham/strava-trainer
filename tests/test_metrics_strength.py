"""Strength metrics — pure-Python arithmetic tested against hand-computed values.

PRD §11.1 acceptance criteria this file directly verifies:
- "Total volume matches a manual spreadsheet calculation exactly, warmups excluded"
- "e1RM matches the Epley formula to 2 d.p."
- "PRs correctly detected and flagged"
"""
from datetime import datetime, timedelta

import pytest

from app.metrics.strength import (
    build_exercise_performance,
    build_strength_workout_context,
    classify_rep_range,
    compute_fatigue_dropoff_pct,
    compute_muscle_group_volumes,
    compute_push_pull_ratio,
    compute_upper_lower_ratio,
    compute_volume_kg,
    detect_prs,
    epley_e1rm,
    brzycki_e1rm,
    build_set_record,
    is_bilateral_dumbbell,
)
from app.models.enums import RecordType, RepRange, SetType
from app.models.history import ExerciseBests, PreviousOccurrence, SessionBests
from app.models.parsed import RawParsedExercise, RawParsedSet, RawParsedWorkout


def test_epley_formula_matches_appendix_a():
    # w * (1 + reps/30)
    assert epley_e1rm(60, 8) == pytest.approx(76.0, abs=0.01)
    assert epley_e1rm(100, 5) == pytest.approx(116.6667, abs=0.01)
    assert epley_e1rm(60, 13) is None  # reps > 12 -> formula not applied


def test_brzycki_formula_matches_appendix_a():
    # w * 36 / (37 - reps)
    assert brzycki_e1rm(60, 8) == pytest.approx(74.4828, abs=0.01)
    assert brzycki_e1rm(60, 13) is None


def test_rep_range_classification_boundaries():
    assert classify_rep_range(1) == RepRange.STRENGTH
    assert classify_rep_range(5) == RepRange.STRENGTH
    assert classify_rep_range(6) == RepRange.HYPERTROPHY
    assert classify_rep_range(12) == RepRange.HYPERTROPHY
    assert classify_rep_range(13) == RepRange.ENDURANCE


def test_volume_excludes_warmup_sets():
    sets = [
        build_set_record(RawParsedSet(weight_kg=40, reps=10, set_type=SetType.WARMUP), 0),
        build_set_record(RawParsedSet(weight_kg=60, reps=8, set_type=SetType.NORMAL), 1),
        build_set_record(RawParsedSet(weight_kg=60, reps=8, set_type=SetType.NORMAL), 2),
        build_set_record(RawParsedSet(weight_kg=60, reps=6, set_type=SetType.NORMAL), 3),
    ]
    # 60*8 + 60*8 + 60*6 = 1320, warmup's 40*10=400 excluded
    assert compute_volume_kg(sets) == pytest.approx(1320.0)


def test_is_bilateral_dumbbell_detection():
    assert is_bilateral_dumbbell("Shoulder Press (Dumbbell)") is True
    assert is_bilateral_dumbbell("Incline Chest Fly (Dumbbell)") is True
    assert is_bilateral_dumbbell("Single Arm Tricep Extension (Dumbbell)") is False
    assert is_bilateral_dumbbell("Bench Press (Barbell)") is False
    assert is_bilateral_dumbbell("Lat Pulldown (Cable)") is False


def test_volume_doubles_for_bilateral_dumbbell_exercises():
    """Hevy logs the weight of ONE dumbbell — a two-handed 'Shoulder Press
    (Dumbbell): 40kg x 8' moves 80kg per rep, not 40kg. Single-arm variants use
    only one dumbbell and must not be doubled."""
    sets = [
        build_set_record(RawParsedSet(weight_kg=40, reps=8), 0),
        build_set_record(RawParsedSet(weight_kg=50, reps=8), 1),
    ]
    assert compute_volume_kg(sets) == pytest.approx(720.0)  # unscaled (e.g. barbell/cable/single-arm)
    assert compute_volume_kg(sets, bilateral_dumbbell=True) == pytest.approx(1440.0)

    perf, _ = build_exercise_performance(
        RawParsedExercise(exercise_name="Shoulder Press (Dumbbell)", sets=[RawParsedSet(weight_kg=40, reps=8), RawParsedSet(weight_kg=50, reps=8)]),
        previous=None, bests=ExerciseBests(), now=datetime(2026, 7, 26),
    )
    assert perf.volume_kg == pytest.approx(1440.0)

    single_arm_perf, _ = build_exercise_performance(
        RawParsedExercise(exercise_name="Single Arm Tricep Extension (Dumbbell)", sets=[RawParsedSet(weight_kg=15, reps=8), RawParsedSet(weight_kg=15, reps=8)]),
        previous=None, bests=ExerciseBests(), now=datetime(2026, 7, 26),
    )
    assert single_arm_perf.volume_kg == pytest.approx(240.0)  # not doubled


def test_fatigue_dropoff_requires_constant_load():
    constant_load = [
        build_set_record(RawParsedSet(weight_kg=60, reps=8), 0),
        build_set_record(RawParsedSet(weight_kg=60, reps=8), 1),
        build_set_record(RawParsedSet(weight_kg=60, reps=6), 2),
    ]
    assert compute_fatigue_dropoff_pct(constant_load) == pytest.approx(25.0)  # (8-6)/8*100

    varying_load = [
        build_set_record(RawParsedSet(weight_kg=60, reps=8), 0),
        build_set_record(RawParsedSet(weight_kg=50, reps=10), 1),
    ]
    assert compute_fatigue_dropoff_pct(varying_load) is None


@pytest.fixture
def bench_press_session():
    return RawParsedExercise(
        exercise_name="Bench Press (Barbell)",
        sets=[
            RawParsedSet(weight_kg=40, reps=10, set_type=SetType.WARMUP),
            RawParsedSet(weight_kg=60, reps=8),
            RawParsedSet(weight_kg=60, reps=8),
            RawParsedSet(weight_kg=60, reps=6),
        ],
    )


def test_exercise_comparison_and_progressive_overload(bench_press_session):
    previous = PreviousOccurrence(
        date=datetime(2026, 7, 19),
        sets=[
            RawParsedSet(weight_kg=60, reps=6),
            RawParsedSet(weight_kg=60, reps=6),
            RawParsedSet(weight_kg=60, reps=6),
        ],
    )
    perf, prs = build_exercise_performance(
        bench_press_session, previous, ExerciseBests(), now=datetime(2026, 7, 26)
    )

    assert perf.volume_kg == pytest.approx(1320.0)
    assert perf.top_set_weight_kg == pytest.approx(60.0)
    assert perf.best_e1rm_kg == pytest.approx(76.0, abs=0.01)

    cmp = perf.comparison
    assert cmp is not None
    assert cmp.days_since_last == 7
    assert cmp.delta_top_set_weight_kg == pytest.approx(0.0)
    assert cmp.delta_volume_kg == pytest.approx(240.0)  # 1320 - 1080
    assert cmp.delta_volume_pct == pytest.approx(22.222, abs=0.01)
    assert cmp.matched_load_note == "last time: 60kg x 6, today: 60kg x 8"
    assert cmp.progressive_overload is True


def test_pr_detection_flags_all_categories(bench_press_session):
    sets = [build_set_record(s, i) for i, s in enumerate(bench_press_session.sets)]
    bests = ExerciseBests(
        heaviest_weight_kg=55,
        best_e1rm_kg=70,
        max_reps_at_weight={60: 5},
        highest_exercise_volume_kg=1000,
    )
    prs = detect_prs("Bench Press (Barbell)", sets, compute_volume_kg(sets), bests)
    record_types = {p.record_type for p in prs}
    assert record_types == {
        RecordType.HEAVIEST_WEIGHT,
        RecordType.BEST_E1RM,
        RecordType.MOST_REPS_AT_WEIGHT,
        RecordType.HIGHEST_EXERCISE_VOLUME,
    }


def test_pr_detection_flags_nothing_when_no_bests_beaten(bench_press_session):
    sets = [build_set_record(s, i) for i, s in enumerate(bench_press_session.sets)]
    bests = ExerciseBests(
        heaviest_weight_kg=100,
        best_e1rm_kg=120,
        max_reps_at_weight={60: 20},
        highest_exercise_volume_kg=5000,
    )
    prs = detect_prs("Bench Press (Barbell)", sets, compute_volume_kg(sets), bests)
    assert prs == []


def test_muscle_group_volume_weighting_and_unknown_exercise_warning():
    from app.models.workout import ExercisePerformance

    bench = ExercisePerformance(
        exercise_name="Bench Press (Barbell)",
        exercise_template_key="bench_press",
        sets=[],
        working_set_count=3,
        total_reps=22,
        volume_kg=1320.0,
        top_set_weight_kg=60.0,
        best_e1rm_kg=76.0,
        fatigue_dropoff_pct=25.0,
    )
    mystery = ExercisePerformance(
        exercise_name="Cable Woodchop Thingamajig",
        exercise_template_key="cable_woodchop_thingamajig",
        sets=[],
        working_set_count=2,
        total_reps=20,
        volume_kg=200.0,
        top_set_weight_kg=20.0,
        best_e1rm_kg=None,
        fatigue_dropoff_pct=None,
    )

    volumes, warnings = compute_muscle_group_volumes([bench, mystery])
    by_group = {v.muscle_group: v.volume_kg for v in volumes}

    assert by_group["chest"] == pytest.approx(1320.0)  # primary, weight 1.0
    assert by_group["triceps"] == pytest.approx(660.0)  # secondary, weight 0.5
    assert by_group["shoulders"] == pytest.approx(660.0)
    assert "cable_woodchop_thingamajig" not in by_group
    assert any("Cable Woodchop Thingamajig" in w for w in warnings)


def test_full_session_context_totals_ratios_and_session_pr():
    raw = RawParsedWorkout(
        title="Push/Pull Day",
        exercises=[
            RawParsedExercise(
                exercise_name="Bench Press (Barbell)",
                sets=[
                    RawParsedSet(weight_kg=40, reps=10, set_type=SetType.WARMUP),
                    RawParsedSet(weight_kg=60, reps=8),
                    RawParsedSet(weight_kg=60, reps=8),
                    RawParsedSet(weight_kg=60, reps=6),
                ],
            ),
            RawParsedExercise(
                exercise_name="Barbell Row",
                sets=[
                    RawParsedSet(weight_kg=50, reps=10),
                    RawParsedSet(weight_kg=50, reps=10),
                ],
            ),
        ],
    )

    ctx = build_strength_workout_context(
        raw,
        strava_activity_id=123,
        start_time=datetime(2026, 7, 26, 7, 0, 0),
        duration_s=3600,
        previous_occurrences={},
        exercise_bests={},
        session_bests=SessionBests(highest_session_volume_kg=2000),
    )

    # 1320 (bench) + 1000 (row) = 2320
    assert ctx.total_volume_kg == pytest.approx(2320.0)
    assert ctx.working_set_count == 5
    assert ctx.density_kg_per_min == pytest.approx(2320.0 / 60, abs=0.01)

    assert ctx.push_pull_ratio == pytest.approx(2640.0 / 1500.0, abs=0.01)
    assert ctx.upper_lower_ratio is None  # no lower-body exercise logged

    session_prs = [p for p in ctx.personal_records if p.exercise_name == "__session__"]
    assert len(session_prs) == 1
    assert session_prs[0].value == pytest.approx(2320.0)
