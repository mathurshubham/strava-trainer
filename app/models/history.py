"""Historical inputs the strength-metrics engine needs but doesn't fetch itself.

Keeping app/metrics/strength.py pure (no DB calls) means every function in it
is unit-testable with plain fixtures. Whatever fetches these from Postgres
(app/agents/data_agent.py) is a separate, thin, testable-by-mocking layer.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.models.parsed import RawParsedSet


class PreviousOccurrence(BaseModel):
    date: datetime
    sets: list[RawParsedSet]  # working sets only, from that prior session


class ExerciseBests(BaseModel):
    heaviest_weight_kg: float | None = None
    best_e1rm_kg: float | None = None
    max_reps_at_weight: dict[float, int] = {}  # weight_kg -> most reps ever performed at (>=) that weight
    highest_exercise_volume_kg: float | None = None


class SessionBests(BaseModel):
    highest_session_volume_kg: float | None = None
