"""All weight-training arithmetic. Pure Python, zero LLM calls — PRD §3.3 [5], §4.1.

"Any prompt that asks the model to add things up is a bug." Every number the
Strength Analyst ever sees was computed by a function in this file.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.metrics.muscle_mapping import (
    LOWER_GROUPS,
    PULL_GROUPS,
    PUSH_GROUPS,
    UPPER_GROUPS,
    muscle_groups_for,
)
from app.models.enums import RecordType, RepRange, SetType
from app.models.history import ExerciseBests, PreviousOccurrence, SessionBests
from app.models.parsed import RawParsedExercise, RawParsedSet, RawParsedWorkout
from app.models.workout import (
    ExerciseComparison,
    ExercisePerformance,
    MuscleGroupVolume,
    PersonalRecord,
    SetRecord,
    StrengthWorkoutContext,
)

# Appendix A
_E1RM_MAX_REPS = 12
_DISAGREEMENT_THRESHOLD_PCT = 5.0
_FATIGUE_EXCESSIVE_PCT = 30.0
_FATIGUE_TOO_LIGHT_PCT = 10.0
_WEIGHT_MATCH_TOLERANCE_KG = 0.01

# Hevy (and every other tracker) logs the weight of a SINGLE dumbbell for
# two-handed dumbbell lifts — "Shoulder Press (Dumbbell): 40kg x 8" means two
# 40kg dumbbells, i.e. 80kg actually moved per rep. Tonnage/volume must count
# both, or every bilateral dumbbell exercise silently underreports by half.
# Single-arm/unilateral variants use one dumbbell and must NOT be doubled.
_UNILATERAL_MARKERS = ("single arm", "single-arm", "single leg", "single-leg", "one arm", "one-arm", "unilateral")


def is_bilateral_dumbbell(exercise_name: str) -> bool:
    name = exercise_name.lower()
    if "dumbbell" not in name:
        return False
    return not any(marker in name for marker in _UNILATERAL_MARKERS)


class MuscleResolver(Protocol):
    """Anything that maps an exercise name to ({group: weight}, found) — the
    static table, or the LLM-backed MuscleMapResolver."""

    def __call__(self, exercise_name: str) -> tuple[dict[str, float], bool]: ...


def epley_e1rm(weight_kg: float, reps: int) -> float | None:
    if reps <= 0 or reps > _E1RM_MAX_REPS:
        return None
    return weight_kg * (1 + reps / 30)


def brzycki_e1rm(weight_kg: float, reps: int) -> float | None:
    if reps <= 0 or reps > _E1RM_MAX_REPS or reps == 37:
        return None
    return weight_kg * 36 / (37 - reps)


def classify_rep_range(reps: int) -> RepRange:
    if reps <= 5:
        return RepRange.STRENGTH
    if reps <= 12:
        return RepRange.HYPERTROPHY
    return RepRange.ENDURANCE


def build_set_record(raw: RawParsedSet, set_index: int) -> SetRecord:
    epley = epley_e1rm(raw.weight_kg, raw.reps)
    brzycki = brzycki_e1rm(raw.weight_kg, raw.reps)
    disagreement = False
    if epley is not None and brzycki is not None and epley > 0:
        disagreement = abs(epley - brzycki) / epley * 100 > _DISAGREEMENT_THRESHOLD_PCT
    return SetRecord(
        set_index=set_index,
        weight_kg=raw.weight_kg,
        reps=raw.reps,
        set_type=raw.set_type,
        e1rm_epley_kg=epley,
        e1rm_brzycki_kg=brzycki,
        e1rm_disagreement=disagreement,
        rep_range=classify_rep_range(raw.reps),
    )


def working_sets(sets: list[SetRecord]) -> list[SetRecord]:
    """PRD §2.2: warmup sets must be excluded from every working-volume calculation."""
    return [s for s in sets if s.set_type != SetType.WARMUP]


def compute_volume_kg(sets: list[SetRecord], *, bilateral_dumbbell: bool = False) -> float:
    multiplier = 2 if bilateral_dumbbell else 1
    return sum(s.weight_kg * s.reps * multiplier for s in working_sets(sets))


def compute_fatigue_dropoff_pct(sets: list[SetRecord]) -> float | None:
    """% rep drop-off, set 1 -> last set, only meaningful when load was held constant."""
    ws = working_sets(sets)
    if len(ws) < 2:
        return None
    first, last = ws[0], ws[-1]
    if abs(first.weight_kg - last.weight_kg) > _WEIGHT_MATCH_TOLERANCE_KG:
        return None  # load wasn't constant — the metric doesn't apply
    if first.reps == 0:
        return None
    return (first.reps - last.reps) / first.reps * 100


def fatigue_flag(dropoff_pct: float | None) -> str | None:
    if dropoff_pct is None:
        return None
    if dropoff_pct > _FATIGUE_EXCESSIVE_PCT:
        return "excessive"
    if dropoff_pct < _FATIGUE_TOO_LIGHT_PCT:
        return "load_likely_too_light"
    return "normal"


def best_e1rm(sets: list[SetRecord]) -> float | None:
    values = [s.e1rm_epley_kg for s in working_sets(sets) if s.e1rm_epley_kg is not None]
    return max(values) if values else None


def top_set_weight(sets: list[SetRecord]) -> float:
    ws = working_sets(sets)
    return max((s.weight_kg for s in ws), default=0.0)


def compute_comparison(
    current_sets: list[SetRecord], previous: PreviousOccurrence | None, now: datetime, *, bilateral_dumbbell: bool = False
) -> ExerciseComparison | None:
    if previous is None:
        return None

    prev_set_records = [build_set_record(s, i) for i, s in enumerate(previous.sets)]
    prev_top = top_set_weight(prev_set_records)
    prev_e1rm = best_e1rm(prev_set_records)
    prev_volume = compute_volume_kg(prev_set_records, bilateral_dumbbell=bilateral_dumbbell)

    cur_top = top_set_weight(current_sets)
    cur_e1rm = best_e1rm(current_sets)
    cur_volume = compute_volume_kg(current_sets, bilateral_dumbbell=bilateral_dumbbell)

    delta_top = cur_top - prev_top
    delta_top_pct = (delta_top / prev_top * 100) if prev_top else 0.0
    delta_e1rm = (cur_e1rm or 0) - (prev_e1rm or 0)
    delta_e1rm_pct = (delta_e1rm / prev_e1rm * 100) if prev_e1rm else 0.0
    delta_volume = cur_volume - prev_volume
    delta_volume_pct = (delta_volume / prev_volume * 100) if prev_volume else 0.0

    matched_note = _matched_load_note(current_sets, prev_set_records)

    progressive_overload = (
        (delta_top >= 0 and _reps_at_weight(current_sets, cur_top) >= _reps_at_weight(prev_set_records, prev_top))
        or (cur_top <= prev_top and delta_volume > 0)
        or delta_volume > 0
    )

    return ExerciseComparison(
        previous_date=previous.date,
        days_since_last=(now - previous.date).days,
        delta_top_set_weight_kg=delta_top,
        delta_top_set_weight_pct=delta_top_pct,
        delta_best_e1rm_kg=delta_e1rm,
        delta_best_e1rm_pct=delta_e1rm_pct,
        delta_volume_kg=delta_volume,
        delta_volume_pct=delta_volume_pct,
        matched_load_note=matched_note,
        progressive_overload=progressive_overload,
    )


def _reps_at_weight(sets: list[SetRecord], weight_kg: float) -> int:
    matches = [s.reps for s in working_sets(sets) if abs(s.weight_kg - weight_kg) <= _WEIGHT_MATCH_TOLERANCE_KG]
    return max(matches, default=0)


def _matched_load_note(current: list[SetRecord], previous: list[SetRecord]) -> str | None:
    """'last time: 80kg x 8, today: 80kg x 10' — only when a matching load exists in both."""
    prev_by_weight = {round(s.weight_kg, 2): s.reps for s in working_sets(previous)}
    for s in working_sets(current):
        w = round(s.weight_kg, 2)
        if w in prev_by_weight:
            return f"last time: {w:g}kg x {prev_by_weight[w]}, today: {w:g}kg x {s.reps}"
    return None


def detect_prs(
    exercise_name: str, sets: list[SetRecord], volume_kg: float, bests: ExerciseBests
) -> list[PersonalRecord]:
    prs: list[PersonalRecord] = []
    ws = working_sets(sets)
    now = datetime.utcnow()

    top = top_set_weight(sets)
    if bests.heaviest_weight_kg is None or top > bests.heaviest_weight_kg:
        prs.append(PersonalRecord(exercise_name=exercise_name, record_type=RecordType.HEAVIEST_WEIGHT, value=top, achieved_at=now))

    e1rm = best_e1rm(sets)
    if e1rm is not None and (bests.best_e1rm_kg is None or e1rm > bests.best_e1rm_kg):
        prs.append(PersonalRecord(exercise_name=exercise_name, record_type=RecordType.BEST_E1RM, value=e1rm, achieved_at=now))

    for s in ws:
        prior_best_reps = bests.max_reps_at_weight.get(round(s.weight_kg, 2), 0)
        if s.reps > prior_best_reps:
            prs.append(
                PersonalRecord(
                    exercise_name=exercise_name,
                    record_type=RecordType.MOST_REPS_AT_WEIGHT,
                    value=s.reps,
                    achieved_at=now,
                )
            )
            break  # one flag per session is enough signal; avoid spamming per-set

    if bests.highest_exercise_volume_kg is None or volume_kg > bests.highest_exercise_volume_kg:
        prs.append(
            PersonalRecord(exercise_name=exercise_name, record_type=RecordType.HIGHEST_EXERCISE_VOLUME, value=volume_kg, achieved_at=now)
        )

    return prs


def detect_session_pr(total_volume_kg: float, bests: SessionBests) -> PersonalRecord | None:
    if bests.highest_session_volume_kg is None or total_volume_kg > bests.highest_session_volume_kg:
        return PersonalRecord(
            exercise_name="__session__",
            record_type=RecordType.HIGHEST_SESSION_VOLUME,
            value=total_volume_kg,
            achieved_at=datetime.utcnow(),
        )
    return None


def build_exercise_performance(
    raw: RawParsedExercise, previous: PreviousOccurrence | None, bests: ExerciseBests, now: datetime
) -> tuple[ExercisePerformance, list[PersonalRecord]]:
    sets = [build_set_record(s, i) for i, s in enumerate(raw.sets)]
    ws = working_sets(sets)
    bilateral = is_bilateral_dumbbell(raw.exercise_name)
    volume = compute_volume_kg(sets, bilateral_dumbbell=bilateral)

    perf = ExercisePerformance(
        exercise_name=raw.exercise_name,
        exercise_template_key=_normalize_key(raw.exercise_name),
        sets=sets,
        working_set_count=len(ws),
        total_reps=sum(s.reps for s in ws),
        volume_kg=volume,
        top_set_weight_kg=top_set_weight(sets),
        best_e1rm_kg=best_e1rm(sets),
        fatigue_dropoff_pct=compute_fatigue_dropoff_pct(sets),
        comparison=compute_comparison(sets, previous, now, bilateral_dumbbell=bilateral),
    )
    prs = detect_prs(raw.exercise_name, sets, volume, bests)
    return perf, prs


def _normalize_key(name: str) -> str:
    from app.metrics.muscle_mapping import normalize_exercise_name

    return normalize_exercise_name(name)


def compute_muscle_group_volumes(
    exercises: list[ExercisePerformance],
    resolver: MuscleResolver | None = None,
) -> tuple[list[MuscleGroupVolume], list[str]]:
    """`resolver` defaults to the static JSON table, which keeps this module pure
    and DB-free for unit tests. Production passes a MuscleMapResolver, which adds
    the learned-mapping and LLM tiers behind the same callable signature."""
    lookup = resolver or muscle_groups_for
    totals: dict[str, float] = {}
    working_sets_totals: dict[str, float] = {}
    warnings: list[str] = []

    for ex in exercises:
        weights, found = lookup(ex.exercise_name)
        if not found:
            warnings.append(f"No muscle-group mapping for '{ex.exercise_name}' — excluded from muscle-group volume.")
            continue
        for group, weight in weights.items():
            totals[group] = totals.get(group, 0.0) + ex.volume_kg * weight
            working_sets_totals[group] = working_sets_totals.get(group, 0.0) + ex.working_set_count * weight

    result = [
        MuscleGroupVolume(muscle_group=group, volume_kg=volume, working_sets=working_sets_totals.get(group, 0.0))
        for group, volume in sorted(totals.items(), key=lambda kv: -kv[1])
    ]
    return result, warnings


def compute_push_pull_ratio(muscle_volumes: list[MuscleGroupVolume]) -> float | None:
    push = sum(m.volume_kg for m in muscle_volumes if m.muscle_group in PUSH_GROUPS)
    pull = sum(m.volume_kg for m in muscle_volumes if m.muscle_group in PULL_GROUPS)
    if pull == 0:
        return None
    return push / pull


def compute_upper_lower_ratio(muscle_volumes: list[MuscleGroupVolume]) -> float | None:
    upper = sum(m.volume_kg for m in muscle_volumes if m.muscle_group in UPPER_GROUPS)
    lower = sum(m.volume_kg for m in muscle_volumes if m.muscle_group in LOWER_GROUPS)
    if lower == 0:
        return None
    return upper / lower


def build_strength_workout_context(
    raw: RawParsedWorkout,
    strava_activity_id: int,
    start_time: datetime,
    duration_s: int,
    previous_occurrences: dict[str, PreviousOccurrence],
    exercise_bests: dict[str, ExerciseBests],
    session_bests: SessionBests,
    resolver: MuscleResolver | None = None,
) -> StrengthWorkoutContext:
    """`previous_occurrences` / `exercise_bests` are keyed by the normalized exercise key
    (app.metrics.muscle_mapping.normalize_exercise_name) so callers can build them
    from a Postgres lookup without this function knowing anything about SQL."""
    now = start_time
    exercises: list[ExercisePerformance] = []
    all_prs: list[PersonalRecord] = []

    for raw_ex in raw.exercises:
        key = _normalize_key(raw_ex.exercise_name)
        perf, prs = build_exercise_performance(
            raw_ex,
            previous_occurrences.get(key),
            exercise_bests.get(key, ExerciseBests()),
            now,
        )
        exercises.append(perf)
        all_prs.extend(prs)

    total_volume = sum(e.volume_kg for e in exercises)
    working_set_count = sum(e.working_set_count for e in exercises)
    total_reps = sum(e.total_reps for e in exercises)
    working_minutes = duration_s / 60 if duration_s else 0
    density = (total_volume / working_minutes) if working_minutes else 0.0

    muscle_volumes, mv_warnings = compute_muscle_group_volumes(exercises, resolver)

    session_pr = detect_session_pr(total_volume, session_bests)
    if session_pr:
        all_prs.append(session_pr)

    all_sets = [s for e in exercises for s in e.sets]
    rpe_values = [s.rpe for s in all_sets if s.rpe is not None]
    rest_values = [s.rest_seconds for s in all_sets if s.rest_seconds is not None]

    return StrengthWorkoutContext(
        strava_activity_id=strava_activity_id,
        title=raw.title,
        start_time=start_time,
        duration_s=duration_s,
        exercises=exercises,
        total_volume_kg=total_volume,
        working_set_count=working_set_count,
        total_reps=total_reps,
        density_kg_per_min=density,
        push_pull_ratio=compute_push_pull_ratio(muscle_volumes),
        upper_lower_ratio=compute_upper_lower_ratio(muscle_volumes),
        avg_rpe=(sum(rpe_values) / len(rpe_values)) if rpe_values else None,
        avg_rest_seconds=(sum(rest_values) / len(rest_values)) if rest_values else None,
        volume_by_muscle_group=muscle_volumes,
        personal_records=all_prs,
        parser_warnings=raw.parser_warnings + mv_warnings,
    )
