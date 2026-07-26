"""Tests for the PROVISIONAL Hevy-description parser (see the module docstring
and DECISIONS.md). These fixtures are hand-written against the commonly
observed Hevy -> Strava export shape, NOT a confirmed real export — replace
with real anonymised samples in tests/fixtures/ as soon as they're available,
per PRD Appendix C.2 ('do not write tests against invented JSON')."""
import pytest

from app.models.enums import SetType
from app.parsers.hevy_strava_description import parse_hevy_strava_description


def test_parses_plain_weight_x_reps_lines():
    description = """Bench Press (Barbell)
60kg x 10
65kg x 8
65kg x 8

Incline Dumbbell Press
24kg x 12
26kg x 10
"""
    workout = parse_hevy_strava_description(description, activity_title="Push Day A")
    assert workout.title == "Push Day A"
    assert len(workout.exercises) == 2

    bench = workout.exercises[0]
    assert bench.exercise_name == "Bench Press (Barbell)"
    assert [s.weight_kg for s in bench.sets] == [60, 65, 65]
    assert [s.reps for s in bench.sets] == [10, 8, 8]
    assert all(s.set_type == SetType.NORMAL for s in bench.sets)


def test_parses_set_n_prefix_and_tags():
    description = """Squat (Barbell)
Set 1: 40kg x 10 (warm up)
Set 2: 100kg x 5
Set 3: 100kg x 5 (failure)
"""
    workout = parse_hevy_strava_description(description, activity_title="Leg Day")
    sets = workout.exercises[0].sets
    assert sets[0].set_type == SetType.WARMUP
    assert sets[1].set_type == SetType.NORMAL
    assert sets[2].set_type == SetType.FAILURE


def test_converts_pounds_to_kg():
    description = """Deadlift (Barbell)
225lbs x 5
"""
    workout = parse_hevy_strava_description(description, activity_title="Pull Day")
    assert workout.exercises[0].sets[0].weight_kg == pytest.approx(225 * 0.45359237, abs=0.01)


def test_ignores_boilerplate_lines():
    description = """Bench Press (Barbell)
60kg x 10

Powered by Hevy https://hevy.com
"""
    workout = parse_hevy_strava_description(description, activity_title="Push Day A")
    assert len(workout.exercises) == 1
    assert not any("Powered by Hevy" in w for w in workout.parser_warnings)


def test_warns_on_exercise_with_no_parseable_sets():
    description = """Mystery Exercise
some unparseable note here
"""
    workout = parse_hevy_strava_description(description, activity_title="Odd Day")
    # A name with no parseable sets is dropped, not emitted as a zero-set exercise:
    # in real descriptions those are header/footer lines, and emitting them sent
    # phantom exercises downstream into muscle-mapping and history.
    assert workout.exercises == []
    assert any("Mystery Exercise" in w for w in workout.parser_warnings)


def test_warns_when_nothing_parses_at_all():
    workout = parse_hevy_strava_description("", activity_title="Empty")
    assert workout.exercises == []
    assert workout.parser_warnings
