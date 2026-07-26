"""Parser tests against REAL Hevy->Strava descriptions.

tests/fixtures/hevy_real_*.txt were captured verbatim from the athlete's own
Strava activities on 2026-07-26 — they are the ground truth DECISIONS.md asked
for, replacing the guessed format the parser was originally written against.

The two bugs these lock down were both found by running the parser on real
data for the first time:
  1. "Logged with hevyapp.com" was parsed as an exercise (the boilerplate
     pattern matched `hevy.com`, but the real footer says `hevyapp.com`).
  2. A name with no sets under it was emitted as a zero-set exercise.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.parsers.hevy_strava_description import parse_hevy_strava_description

FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("hevy_real_*.txt"))


def test_real_fixtures_are_present():
    assert FIXTURES, "real Hevy fixtures are missing — re-capture them from Strava"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_real_description_parses_cleanly(path: Path):
    parsed = parse_hevy_strava_description(path.read_text(), "Workout")

    assert parsed.exercises, f"{path.name} produced no exercises"
    assert parsed.parser_warnings == [], f"{path.name} produced warnings: {parsed.parser_warnings}"

    for ex in parsed.exercises:
        assert ex.sets, f"{ex.exercise_name} has no sets"
        assert "hevy" not in ex.exercise_name.lower(), "boilerplate leaked in as an exercise"
        for s in ex.sets:
            assert s.weight_kg > 0
            assert s.reps > 0


def test_hevyapp_footer_is_not_an_exercise():
    """Regression: `hevy\\.com` did not match the real `hevyapp.com` footer."""
    parsed = parse_hevy_strava_description(
        "Logged with hevyapp.com\n\nBench Press (Barbell)\nSet 1: 60 kg x 10 \n", "W"
    )
    assert [e.exercise_name for e in parsed.exercises] == ["Bench Press (Barbell)"]
    assert parsed.parser_warnings == []


def test_exact_values_from_a_known_fixture():
    """Spot-check real numbers end to end, including the ' kg ' spacing and
    trailing whitespace that the real export uses."""
    parsed = parse_hevy_strava_description((Path(__file__).parent / "fixtures" / "hevy_real_01.txt").read_text(), "W")

    assert [e.exercise_name for e in parsed.exercises] == [
        "Shoulder Press (Dumbbell)",
        "Lateral Raise (Dumbbell)",
        "Shrug (Dumbbell)",
    ]
    press = parsed.exercises[0]
    assert [(s.weight_kg, s.reps) for s in press.sets] == [(40.0, 8), (50.0, 8), (60.0, 4), (60.0, 6)]
    assert sum(len(e.sets) for e in parsed.exercises) == 11


def test_decimal_weights_survive():
    """hevy_real_02 contains 58.5 kg / 71.5 kg — plate math must not be rounded away."""
    parsed = parse_hevy_strava_description((Path(__file__).parent / "fixtures" / "hevy_real_02.txt").read_text(), "W")
    pulldown = next(e for e in parsed.exercises if "Lat Pulldown" in e.exercise_name)
    assert [s.weight_kg for s in pulldown.sets] == [58.5, 65.0, 71.5, 71.5]
