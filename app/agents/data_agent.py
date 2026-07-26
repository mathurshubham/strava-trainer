"""[5] Data Agent — pure Python, zero LLM calls (PRD §3.3 [5], Appendix C.3).

Wires together: the Strava-description parser, the strength/endurance metrics
engines, and exercise history — into the canonical WorkoutContext objects the
LLM analysts read. Takes already-fetched Strava API dicts as input rather
than calling the API itself, so every function here is unit-testable with
plain fixtures; the actual HTTP fetching is the worker's job (api/worker.py),
which is the one layer that genuinely needs live credentials to test.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.metrics import endurance as endurance_metrics
from app.metrics import strength as strength_metrics
from app.metrics.muscle_mapping import normalize_exercise_name
from app.models.history import ExerciseBests, PreviousOccurrence, SessionBests
from app.models.profile import AthleteProfile
from app.models.streams import LapRecord, StreamSample
from app.models.workout import EnduranceWorkoutContext, StrengthWorkoutContext
from app.parsers.hevy_strava_description import parse_hevy_strava_description


def parse_strava_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def samples_from_streams(streams_raw: dict) -> list[StreamSample]:
    """Strava's /streams response, key_by_type=true: {"time": {"data": [...]}, ...}.
    All arrays share the same index basis — the i-th element of every stream
    is the same moment — it's the *time deltas between* indices that are
    irregular (PRD §2.3), not the indexing itself."""

    def arr(key: str) -> list | None:
        entry = streams_raw.get(key)
        return entry["data"] if entry else None

    times = arr("time") or []
    hrs, dists, vels = arr("heartrate"), arr("distance"), arr("velocity_smooth")
    grades, cadences, alts, movings = arr("grade_smooth"), arr("cadence"), arr("altitude"), arr("moving")

    return [
        StreamSample(
            time_s=times[i],
            heartrate=hrs[i] if hrs else None,
            distance_m=dists[i] if dists else None,
            velocity_ms=vels[i] if vels else None,
            grade_pct=grades[i] if grades else None,
            cadence=cadences[i] if cadences else None,
            altitude_m=alts[i] if alts else None,
            moving=movings[i] if movings is not None else True,
        )
        for i in range(len(times))
    ]


def laps_from_raw(laps_raw: list[dict]) -> list[LapRecord]:
    return [
        LapRecord(
            lap_index=i,
            distance_m=lap.get("distance", 0),
            moving_time_s=lap.get("moving_time", 0),
            average_heartrate=lap.get("average_heartrate"),
            average_speed_ms=lap.get("average_speed"),
        )
        for i, lap in enumerate(laps_raw)
    ]


def build_strength_context(
    activity: dict,
    *,
    previous_occurrences: dict[str, PreviousOccurrence],
    exercise_bests: dict[str, ExerciseBests],
    session_bests: SessionBests,
    resolver=None,
) -> StrengthWorkoutContext:
    description = activity.get("description") or ""
    title = activity.get("name") or "Workout"
    start_time = parse_strava_datetime(activity["start_date"])
    duration_s = activity.get("elapsed_time", 0)

    parsed = parse_hevy_strava_description(description, title)

    return strength_metrics.build_strength_workout_context(
        parsed,
        strava_activity_id=activity["id"],
        start_time=start_time,
        duration_s=duration_s,
        previous_occurrences=previous_occurrences,
        exercise_bests=exercise_bests,
        session_bests=session_bests,
        resolver=resolver,
    )


def build_endurance_context(
    activity: dict,
    streams_raw: dict,
    laps_raw: list[dict],
    *,
    profile: AthleteProfile,
    median_28d_distance_m: float | None,
    acute_7d_load: float,
    chronic_28d_load: float,
) -> EnduranceWorkoutContext:
    samples = samples_from_streams(streams_raw)
    laps = laps_from_raw(laps_raw)

    boundaries, _zone_source, zone_warnings = endurance_metrics.resolve_zone_boundaries(profile.max_hr, profile.lthr)
    zone_times = endurance_metrics.compute_time_in_zone(samples, boundaries)
    zone_pcts = {z.zone: z.pct_of_moving_time for z in zone_times}

    splits = endurance_metrics.splits_from_stream(samples)
    split_paces = [s.pace_s_per_km for s in splits]

    moving_time_s = activity.get("moving_time", 0)
    distance_m = activity.get("distance", 0)
    avg_hr = activity.get("average_heartrate")

    longest_z4plus = endurance_metrics.longest_continuous_block_s(samples, boundaries, {"Z4 Threshold", "Z5 VO2max"})
    longest_z3 = endurance_metrics.longest_continuous_block_s(samples, boundaries, {"Z3 Tempo"})

    classification, confidence, evidence = endurance_metrics.classify_run(
        moving_time_s=moving_time_s,
        distance_m=distance_m,
        zone_pcts=zone_pcts,
        longest_z4plus_block_s=longest_z4plus,
        longest_z3_block_s=longest_z3,
        laps=laps,
        split_paces_s_per_km=split_paces,
        median_28d_distance_m=median_28d_distance_m,
        workout_type=activity.get("workout_type"),
        is_best_effort_pr=bool(activity.get("best_efforts")),
    )

    zone1_2_pct = zone_pcts.get("Z1 Recovery", 0) + zone_pcts.get("Z2 Aerobic", 0)
    load_context = endurance_metrics.build_load_context(
        acute_7d_load, chronic_28d_load, weekly_distance_km=acute_7d_load / 1000, zone1_2_pct_28d=zone1_2_pct
    )

    return EnduranceWorkoutContext(
        strava_activity_id=activity["id"],
        sport_type=activity.get("sport_type", activity.get("type", "Run")),
        start_time=parse_strava_datetime(activity["start_date"]),
        distance_m=distance_m,
        moving_time_s=moving_time_s,
        elapsed_time_s=activity.get("elapsed_time", moving_time_s),
        avg_hr=avg_hr,
        max_hr_observed=activity.get("max_heartrate"),
        elevation_gain_m=activity.get("total_elevation_gain"),
        zone_distribution=zone_times,
        splits=splits,
        pace_cv=endurance_metrics.pace_variability_cv(split_paces),
        grade_adjusted_pace_s_per_km=endurance_metrics.grade_adjusted_pace_s_per_km(samples),
        decoupling_pct=endurance_metrics.compute_decoupling_pct(samples),
        efficiency_factor=endurance_metrics.efficiency_factor(distance_m, moving_time_s, avg_hr),
        cadence_spm=endurance_metrics.cadence_spm(activity["average_cadence"]) if activity.get("average_cadence") else None,
        cadence_cv=None,
        trimp=endurance_metrics.compute_trimp(moving_time_s / 60, avg_hr, profile.resting_hr, profile.max_hr),
        suffer_score=activity.get("suffer_score"),
        zone1_fade_pct=endurance_metrics.zone1_fade_pct(samples),
        classification=classification,
        classification_confidence=confidence,
        classification_evidence=evidence,
        load_context=load_context,
        stream_warnings=zone_warnings,
    )
