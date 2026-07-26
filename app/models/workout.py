"""Canonical, pre-computed workout representations.

Every number on these models is computed by app/metrics/*.py — pure Python,
zero LLM. Agents only ever read these; they never receive raw API JSON and
never compute anything themselves. PRD §3.3 [5], Appendix C.3.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.models.enums import RecordType, RepRange, RunClassification, SetType


class SetRecord(BaseModel):
    set_index: int
    weight_kg: float
    reps: int
    set_type: SetType = SetType.NORMAL
    rpe: float | None = None
    rest_seconds: int | None = None
    e1rm_epley_kg: float | None = None
    e1rm_brzycki_kg: float | None = None
    e1rm_disagreement: bool = False  # True when Epley/Brzycki diverge >5%
    rep_range: RepRange | None = None


class ExerciseComparison(BaseModel):
    previous_date: datetime
    days_since_last: int
    delta_top_set_weight_kg: float
    delta_top_set_weight_pct: float
    delta_best_e1rm_kg: float
    delta_best_e1rm_pct: float
    delta_volume_kg: float
    delta_volume_pct: float
    matched_load_note: str | None = None  # e.g. "last time: 80kg x 8, today: 80kg x 10"
    progressive_overload: bool


class PersonalRecord(BaseModel):
    exercise_name: str
    record_type: RecordType
    value: float
    achieved_at: datetime


class ExercisePerformance(BaseModel):
    exercise_name: str
    exercise_template_key: str  # normalized key into the muscle-group mapping
    sets: list[SetRecord]
    working_set_count: int
    total_reps: int
    volume_kg: float
    top_set_weight_kg: float
    best_e1rm_kg: float | None
    fatigue_dropoff_pct: float | None  # % rep drop-off, set1 -> last set at constant load
    comparison: ExerciseComparison | None = None


class MuscleGroupVolume(BaseModel):
    muscle_group: str
    volume_kg: float
    working_sets: float  # weighted (secondary muscle = 0.5)


class StrengthWorkoutContext(BaseModel):
    strava_activity_id: int
    title: str
    start_time: datetime
    duration_s: int
    exercises: list[ExercisePerformance]

    total_volume_kg: float
    working_set_count: int
    total_reps: int
    density_kg_per_min: float
    push_pull_ratio: float | None
    upper_lower_ratio: float | None
    avg_rpe: float | None
    avg_rest_seconds: float | None

    volume_by_muscle_group: list[MuscleGroupVolume]
    personal_records: list[PersonalRecord]

    weekly_sets_per_muscle_group: dict[str, float] = {}
    volume_trend_flags: dict[str, str] = {}  # exercise_key -> "plateau" | "trending_up" | ...
    frequency_gaps: list[str] = []  # muscle groups trained <2x/week or below set-volume band

    parser_warnings: list[str] = []  # e.g. couldn't classify a line, treated as note text


class SplitRecord(BaseModel):
    split_index: int
    distance_m: float
    time_s: float
    pace_s_per_km: float
    avg_hr: float | None = None


class ZoneTime(BaseModel):
    zone: str
    seconds: float
    pct_of_moving_time: float


class SimilarRun(BaseModel):
    strava_activity_id: int
    start_time: datetime
    delta_pace_pct: float
    delta_hr_pct: float
    delta_ef_pct: float


class LoadContext(BaseModel):
    acute_7d_load: float
    chronic_28d_load: float
    acwr: float
    weekly_distance_km: float
    zone1_2_pct_28d: float
    zone3_plus_pct_28d: float


class EnduranceWorkoutContext(BaseModel):
    strava_activity_id: int
    sport_type: str
    start_time: datetime
    distance_m: float
    moving_time_s: float
    elapsed_time_s: float
    avg_hr: float | None
    max_hr_observed: float | None
    elevation_gain_m: float | None

    zone_distribution: list[ZoneTime]
    splits: list[SplitRecord]
    pace_cv: float | None  # coefficient of variation of split paces
    grade_adjusted_pace_s_per_km: float | None
    decoupling_pct: float | None
    efficiency_factor: float | None
    cadence_spm: float | None
    cadence_cv: float | None
    trimp: float | None
    suffer_score: float | None
    zone1_fade_pct: float | None

    classification: RunClassification
    classification_confidence: float
    classification_evidence: list[str]

    similar_runs: list[SimilarRun] = []
    load_context: LoadContext | None = None

    stream_warnings: list[str] = []  # e.g. "irregular sampling detected, Δt-weighted"
