"""Parses a Hevy-synced Strava activity description into structured sets.

VALIDATED 2026-07-26 against four real Hevy->Strava exports from the athlete's
own account (captured verbatim in tests/fixtures/hevy_real_*.txt). The real
format is:

    Logged with hevyapp.com          <- footer, must be ignored

    Shoulder Press (Dumbbell)
    Set 1: 40 kg x 8
    Set 2: 50 kg x 8

i.e. a footer line, then an exercise name on its own line, then one line per
set as "Set N: <weight> kg x <reps>", blocks separated by blank lines. Note the
space between the number and the unit, and the trailing space on every set line.

The parser stays deliberately looser than that exact shape — it also accepts
"60kg x 10" without the "Set N:" prefix, lbs, decimals, and set-type tags like
"(warm up)" / "(failure)" / "(drop set)" — since Hevy's format may vary by
export path and only four samples were available.

Anything it can't classify is recorded in `parser_warnings` rather than
silently dropped or guessed. A name with no parseable set lines under it is
dropped with a warning rather than emitted as a zero-set exercise; in real
descriptions those are header/footer lines, not exercises.
"""
from __future__ import annotations

import re

from app.models.enums import SetType
from app.models.parsed import RawParsedExercise, RawParsedSet, RawParsedWorkout

_LB_TO_KG = 0.45359237

_SET_LINE_RE = re.compile(
    r"^\s*(?:set\s*\d+\s*[:.]?\s*)?"
    r"(?P<weight>\d+(?:\.\d+)?)\s*(?P<unit>kg|lbs?|kilograms?|pounds?)\b"
    r"\s*x\s*(?P<reps>\d+)"
    r"\s*(?P<tag>.*)$",
    re.IGNORECASE,
)

# Real exports lead with "Logged with hevyapp.com" — note `hevyapp.com`, which an
# anchored `hevy\.com` does NOT match. Match the brand anywhere instead; no real
# exercise name contains "hevy", so this is safe to keep broad.
_BOILERPLATE_RE = re.compile(r"\bhevy|^strava$", re.IGNORECASE)

_TAG_TO_SET_TYPE: list[tuple[re.Pattern[str], SetType]] = [
    (re.compile(r"warm", re.IGNORECASE), SetType.WARMUP),
    (re.compile(r"fail", re.IGNORECASE), SetType.FAILURE),
    (re.compile(r"drop", re.IGNORECASE), SetType.DROPSET),
]


def _to_kg(weight: float, unit: str) -> float:
    return weight * _LB_TO_KG if unit.lower().startswith(("lb", "pound")) else weight


def _classify_tag(tag: str) -> SetType:
    for pattern, set_type in _TAG_TO_SET_TYPE:
        if pattern.search(tag):
            return set_type
    return SetType.NORMAL


def parse_hevy_strava_description(description: str, activity_title: str) -> RawParsedWorkout:
    exercises: list[RawParsedExercise] = []
    warnings: list[str] = []
    current_name: str | None = None
    current_sets: list[RawParsedSet] = []

    def flush():
        if current_name is not None:
            if not current_sets:
                # A name with no sets under it carries no training data and is far
                # more likely to be a header/footer line we failed to classify than
                # a real exercise. Warn, but don't emit it — otherwise it reaches
                # muscle-mapping and history as a phantom exercise.
                warnings.append(f"Exercise '{current_name}' had no parseable set lines under it — dropped.")
                return
            exercises.append(RawParsedExercise(exercise_name=current_name, sets=list(current_sets)))

    for raw_line in description.splitlines():
        line = raw_line.strip()
        if not line or _BOILERPLATE_RE.search(line):
            continue

        match = _SET_LINE_RE.match(line)
        if match:
            if current_name is None:
                warnings.append(f"Set line '{line}' appeared before any exercise name — skipped.")
                continue
            weight = _to_kg(float(match.group("weight")), match.group("unit"))
            reps = int(match.group("reps"))
            set_type = _classify_tag(match.group("tag"))
            current_sets.append(RawParsedSet(weight_kg=weight, reps=reps, set_type=set_type))
            continue

        # Not a set line -> treat as the start of a new exercise block.
        flush()
        current_name = line
        current_sets = []

    flush()

    if not exercises:
        warnings.append("No exercises could be parsed from this description at all — check the format assumption in app/parsers/hevy_strava_description.py.")

    return RawParsedWorkout(title=activity_title, exercises=exercises, parser_warnings=warnings)
