"""Intermediate representation between 'raw Strava description text' and the
canonical, metric-enriched StrengthWorkoutContext. The parser (app/parsers/)
produces this; app/metrics/strength.py consumes it and does all arithmetic.
"""
from __future__ import annotations

from pydantic import BaseModel

from app.models.enums import SetType


class RawParsedSet(BaseModel):
    weight_kg: float
    reps: int
    set_type: SetType = SetType.NORMAL


class RawParsedExercise(BaseModel):
    exercise_name: str
    sets: list[RawParsedSet]


class RawParsedWorkout(BaseModel):
    title: str
    exercises: list[RawParsedExercise]
    parser_warnings: list[str] = []
