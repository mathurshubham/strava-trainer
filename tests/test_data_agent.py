"""Tests the Data Agent — pure wiring of parser + metrics + history into the
canonical WorkoutContext objects, using already-fetched (fixture) Strava API
dicts rather than live HTTP. PRD §3.3 [5]: 'zero LLM calls', fully testable."""
from app.agents.data_agent import build_endurance_context, build_strength_context, laps_from_raw, samples_from_streams
from app.models.history import ExerciseBests, SessionBests
from app.models.profile import AthleteProfile


def test_samples_from_streams_aligns_by_index():
    streams_raw = {
        "time": {"data": [0, 10, 20]},
        "heartrate": {"data": [120, 130, 140]},
        "distance": {"data": [0, 50, 100]},
        "moving": {"data": [True, True, False]},
    }
    samples = samples_from_streams(streams_raw)
    assert len(samples) == 3
    assert samples[1].heartrate == 130
    assert samples[1].distance_m == 50
    assert samples[2].moving is False
    assert samples[0].grade_pct is None  # not present in this fixture -> None, not a crash


def test_laps_from_raw():
    laps_raw = [{"distance": 1000, "moving_time": 300, "average_heartrate": 150}]
    laps = laps_from_raw(laps_raw)
    assert laps[0].lap_index == 0
    assert laps[0].distance_m == 1000


def test_build_strength_context_end_to_end():
    activity = {
        "id": 999,
        "name": "Push Day A",
        "start_date": "2026-07-26T07:00:00Z",
        "elapsed_time": 3600,
        "description": "Bench Press (Barbell)\n60kg x 8\n60kg x 8\n60kg x 6\n",
    }
    context = build_strength_context(
        activity,
        previous_occurrences={},
        exercise_bests={"bench_press": ExerciseBests(heaviest_weight_kg=55)},
        session_bests=SessionBests(),
    )
    assert context.strava_activity_id == 999
    assert context.title == "Push Day A"
    assert context.total_volume_kg > 0
    assert any(pr.exercise_name == "Bench Press (Barbell)" for pr in context.personal_records)


def test_build_endurance_context_end_to_end():
    activity = {
        "id": 1000,
        "type": "Run",
        "start_date": "2026-07-26T06:00:00Z",
        "distance": 8000,
        "moving_time": 2400,
        "elapsed_time": 2450,
        "average_heartrate": 145,
        "max_heartrate": 165,
        "total_elevation_gain": 40,
    }
    n = 100
    streams_raw = {
        "time": {"data": [i * 24 for i in range(n)]},
        "heartrate": {"data": [140 + (i % 10) for i in range(n)]},
        "distance": {"data": [i * 80 for i in range(n)]},
        "moving": {"data": [True] * n},
    }
    profile = AthleteProfile(max_hr=190, resting_hr=55)

    context = build_endurance_context(
        activity, streams_raw, [], profile=profile, median_28d_distance_m=8000,
        acute_7d_load=300, chronic_28d_load=1000,
    )
    assert context.strava_activity_id == 1000
    assert context.distance_m == 8000
    assert context.load_context is not None
    assert context.load_context.acwr > 0
    assert sum(z.seconds for z in context.zone_distribution) > 0
