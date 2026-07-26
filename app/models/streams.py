"""Per-second Strava stream samples and lap records — the raw input to endurance metrics."""
from __future__ import annotations

from pydantic import BaseModel


class StreamSample(BaseModel):
    time_s: float
    heartrate: float | None = None
    distance_m: float | None = None  # cumulative
    velocity_ms: float | None = None
    grade_pct: float | None = None
    cadence: float | None = None  # per-leg, as Strava reports it
    altitude_m: float | None = None
    moving: bool = True


class LapRecord(BaseModel):
    lap_index: int
    distance_m: float
    moving_time_s: float
    average_heartrate: float | None = None
    average_speed_ms: float | None = None
