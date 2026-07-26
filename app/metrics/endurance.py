"""All endurance arithmetic. Pure Python, zero LLM calls — same rule as app/metrics/strength.py.

The one gotcha the PRD is loudest about (§2.3): Strava stream samples are NOT
evenly spaced. Time-in-zone must weight each sample by its real Δt to the next
sample, never by counting samples — a multi-minute device gap must contribute
its real duration to whatever zone applies, not "one sample's worth."
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime

from app.models.enums import RunClassification
from app.models.streams import LapRecord, StreamSample
from app.models.workout import LoadContext, SimilarRun, SplitRecord, ZoneTime

ZoneBoundary = tuple[str, float, float]  # (name, lower_bound_bpm, upper_bound_bpm)

_ZONE_NAMES = ["Z1 Recovery", "Z2 Aerobic", "Z3 Tempo", "Z4 Threshold", "Z5 VO2max"]
_HRMAX_PCTS = [(0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 999.0)]
_LTHR_PCTS = [(0.00, 0.81), (0.81, 0.89), (0.89, 0.93), (0.93, 0.99), (0.99, 999.0)]
_ESTIMATED_MAX_HR_DEFAULT = 190  # literature-average fallback; always flagged as an estimate


def zone_boundaries_from_hrmax(max_hr: float) -> list[ZoneBoundary]:
    return [(name, lo * max_hr, hi * max_hr) for name, (lo, hi) in zip(_ZONE_NAMES, _HRMAX_PCTS)]


def zone_boundaries_from_lthr(lthr: float) -> list[ZoneBoundary]:
    return [(name, lo * lthr, hi * lthr) for name, (lo, hi) in zip(_ZONE_NAMES, _LTHR_PCTS)]


def resolve_zone_boundaries(
    max_hr: float | None, lthr: float | None, strava_zone_bounds: list[ZoneBoundary] | None = None
) -> tuple[list[ZoneBoundary], str, list[str]]:
    """PRD §4.2 priority: (1) Strava custom zones, (2) LTHR/MAX_HR from profile, (3) estimate."""
    warnings: list[str] = []
    if strava_zone_bounds:
        return strava_zone_bounds, "strava_custom", warnings
    if lthr:
        return zone_boundaries_from_lthr(lthr), "lthr", warnings
    if max_hr:
        return zone_boundaries_from_hrmax(max_hr), "max_hr", warnings
    warnings.append(
        f"No MAX_HR/LTHR configured — using an estimated max HR of {_ESTIMATED_MAX_HR_DEFAULT} bpm. "
        "Set MAX_HR or LTHR via /profile for accurate zones."
    )
    return zone_boundaries_from_hrmax(_ESTIMATED_MAX_HR_DEFAULT), "estimate", warnings


def classify_zone(hr: float, boundaries: list[ZoneBoundary]) -> str:
    for name, lo, hi in boundaries:
        if lo <= hr < hi:
            return name
    if hr < boundaries[0][1]:
        return boundaries[0][0]
    return boundaries[-1][0]


def compute_time_in_zone(samples: list[StreamSample], boundaries: list[ZoneBoundary]) -> list[ZoneTime]:
    """Δt-weighted time in zone. Never counts samples. Drops intervals where the
    starting sample is not moving or has no HR reading."""
    ordered = sorted(samples, key=lambda s: s.time_s)
    totals: dict[str, float] = {name: 0.0 for name, _, _ in boundaries}
    total_moving = 0.0

    for cur, nxt in zip(ordered, ordered[1:]):
        if not cur.moving or cur.heartrate is None:
            continue
        dt = nxt.time_s - cur.time_s
        if dt <= 0:
            continue
        zone = classify_zone(cur.heartrate, boundaries)
        totals[zone] += dt
        total_moving += dt

    return [
        ZoneTime(zone=name, seconds=totals[name], pct_of_moving_time=(totals[name] / total_moving * 100) if total_moving else 0.0)
        for name, _, _ in boundaries
    ]


def longest_continuous_block_s(samples: list[StreamSample], boundaries: list[ZoneBoundary], zones: set[str]) -> float:
    """Longest single continuous Δt-weighted stretch where the sample's zone is in `zones`."""
    ordered = sorted(samples, key=lambda s: s.time_s)
    longest = 0.0
    current = 0.0
    for cur, nxt in zip(ordered, ordered[1:]):
        if not cur.moving or cur.heartrate is None:
            current = 0.0
            continue
        dt = nxt.time_s - cur.time_s
        if dt <= 0:
            continue
        zone = classify_zone(cur.heartrate, boundaries)
        if zone in zones:
            current += dt
            longest = max(longest, current)
        else:
            current = 0.0
    return longest


def efficiency_factor(distance_m: float, moving_time_s: float, avg_hr: float | None) -> float | None:
    if not avg_hr or avg_hr <= 0 or moving_time_s <= 0:
        return None
    moving_min = moving_time_s / 60
    return (distance_m / moving_min) / avg_hr


def _segment_ef(seg: list[StreamSample]) -> float | None:
    hrs = [s.heartrate for s in seg if s.heartrate is not None]
    dists = [s.distance_m for s in seg if s.distance_m is not None]
    if not hrs or len(dists) < 2:
        return None
    distance = dists[-1] - dists[0]
    duration_s = seg[-1].time_s - seg[0].time_s
    if duration_s <= 0:
        return None
    return efficiency_factor(distance, duration_s, sum(hrs) / len(hrs))


def compute_decoupling_pct(samples: list[StreamSample]) -> float | None:
    """(EF_first_half - EF_second_half) / EF_first_half * 100. Split by time midpoint,
    not sample-count midpoint, since sampling can be irregular."""
    moving = sorted((s for s in samples if s.moving and s.heartrate is not None), key=lambda s: s.time_s)
    if len(moving) < 4:
        return None
    midpoint = moving[0].time_s + (moving[-1].time_s - moving[0].time_s) / 2
    first = [s for s in moving if s.time_s <= midpoint]
    second = [s for s in moving if s.time_s > midpoint]
    ef1, ef2 = _segment_ef(first), _segment_ef(second)
    if not ef1 or not ef2:
        return None
    return (ef1 - ef2) / ef1 * 100


def zone1_fade_pct(samples: list[StreamSample], window_s: float = 600) -> float | None:
    """HR drift in the first `window_s` seconds — a warm-up quality check."""
    moving = sorted((s for s in samples if s.moving and s.heartrate is not None), key=lambda s: s.time_s)
    if not moving:
        return None
    start = moving[0].time_s
    window = [s for s in moving if s.time_s <= start + window_s]
    if len(window) < 2:
        return None
    first_hr, last_hr = window[0].heartrate, window[-1].heartrate
    if not first_hr:
        return None
    return (last_hr - first_hr) / first_hr * 100


def cadence_spm(strava_cadence_per_leg: float) -> float:
    """Strava reports running cadence per leg — double it for steps/min. Appendix A."""
    return strava_cadence_per_leg * 2


def pace_variability_cv(split_paces_s_per_km: list[float]) -> float | None:
    if len(split_paces_s_per_km) < 2:
        return None
    mean = statistics.mean(split_paces_s_per_km)
    if mean == 0:
        return None
    return statistics.stdev(split_paces_s_per_km) / mean


def grade_adjusted_pace_s_per_km(samples: list[StreamSample]) -> float | None:
    """Approximate GAP: cost multiplier ~= 1 + 0.03 * grade%% per interval, distance-weighted.
    Explicitly an approximation — Strava's own GAP algorithm is proprietary (PRD §4.2)."""
    ordered = sorted(samples, key=lambda s: s.time_s)
    total_adjusted_time = 0.0
    total_distance = 0.0
    for cur, nxt in zip(ordered, ordered[1:]):
        if cur.distance_m is None or nxt.distance_m is None:
            continue
        d = nxt.distance_m - cur.distance_m
        dt = nxt.time_s - cur.time_s
        if d <= 0 or dt <= 0:
            continue
        grade = cur.grade_pct or 0.0
        factor = 1 + 0.03 * grade
        total_adjusted_time += dt / max(factor, 0.01)
        total_distance += d
    if total_distance <= 0:
        return None
    return (total_adjusted_time / total_distance) * 1000


def compute_trimp(duration_min: float, avg_hr: float | None, resting_hr: float | None, max_hr: float | None) -> float | None:
    """Banister TRIMP, male coefficients per Appendix A. Needs all three HR inputs."""
    if not avg_hr or not resting_hr or not max_hr or max_hr <= resting_hr:
        return None
    hrr = (avg_hr - resting_hr) / (max_hr - resting_hr)
    if hrr < 0:
        return None
    return duration_min * hrr * 0.64 * math.exp(1.92 * hrr)


def compute_acwr(acute_7d_load: float, chronic_28d_load: float) -> float | None:
    chronic_weekly_avg = chronic_28d_load / 4
    if chronic_weekly_avg <= 0:
        return None
    return acute_7d_load / chronic_weekly_avg


def acwr_flag(acwr: float | None) -> str | None:
    if acwr is None:
        return None
    if acwr > 1.5:
        return "elevated_risk"
    if 0.8 <= acwr <= 1.3:
        return "sweet_spot"
    return "outside_sweet_spot"


def splits_from_stream(samples: list[StreamSample], split_distance_m: float = 1000.0) -> list[SplitRecord]:
    ordered = sorted((s for s in samples if s.distance_m is not None), key=lambda s: s.time_s)
    if not ordered:
        return []
    splits: list[SplitRecord] = []
    split_index = 1
    boundary = split_distance_m
    seg_start = ordered[0]
    seg_hrs: list[float] = []
    for s in ordered:
        if s.heartrate is not None:
            seg_hrs.append(s.heartrate)
        if s.distance_m >= boundary:
            d = s.distance_m - seg_start.distance_m
            t = s.time_s - seg_start.time_s
            if t > 0 and d > 0:
                splits.append(
                    SplitRecord(
                        split_index=split_index,
                        distance_m=d,
                        time_s=t,
                        pace_s_per_km=(t / d) * 1000,
                        avg_hr=(sum(seg_hrs) / len(seg_hrs)) if seg_hrs else None,
                    )
                )
            split_index += 1
            boundary += split_distance_m
            seg_start = s
            seg_hrs = []
    return splits


def classify_run(
    *,
    moving_time_s: float,
    distance_m: float,
    zone_pcts: dict[str, float],
    longest_z4plus_block_s: float,
    longest_z3_block_s: float,
    laps: list[LapRecord],
    split_paces_s_per_km: list[float],
    median_28d_distance_m: float | None,
    workout_type: int | None,
    is_best_effort_pr: bool,
) -> tuple[RunClassification, float, list[str]]:
    """Deterministic classifier, run before any LLM involvement (PRD §4.2). The LLM only
    confirms/labels; every branch here returns its own evidence trail."""
    z1z2 = zone_pcts.get("Z1 Recovery", 0) + zone_pcts.get("Z2 Aerobic", 0)

    if workout_type == 1 or is_best_effort_pr:
        return (
            RunClassification.RACE,
            0.95,
            ["workout_type flagged as race" if workout_type == 1 else "contains a best-effort PR"],
        )

    interval_evidence = _detect_intervals(laps)
    if interval_evidence:
        return RunClassification.INTERVALS, 0.8, interval_evidence

    if 1200 <= longest_z4plus_block_s <= 2400:
        return (
            RunClassification.THRESHOLD,
            0.85,
            [f"{longest_z4plus_block_s / 60:.0f} min continuous in Z4 Threshold"],
        )

    if longest_z3_block_s >= 900:
        return RunClassification.TEMPO, 0.75, [f"{longest_z3_block_s / 60:.0f} min continuous in Z3 Tempo"]

    if len(split_paces_s_per_km) >= 3 and _is_monotonic_decreasing(split_paces_s_per_km):
        return RunClassification.PROGRESSION, 0.7, ["pace improved monotonically across the run (negative split)"]

    is_long_by_duration = moving_time_s >= 90 * 60
    is_long_by_distance = median_28d_distance_m is not None and distance_m >= 1.5 * median_28d_distance_m
    if (is_long_by_duration or is_long_by_distance) and z1z2 >= 60:
        evidence = []
        if is_long_by_duration:
            evidence.append(f"{moving_time_s / 60:.0f} min, ≥ 90 min threshold")
        if is_long_by_distance:
            evidence.append("distance ≥ 1.5× 28-day median")
        return RunClassification.LONG_RUN, 0.75, evidence

    if z1z2 >= 75 and longest_z4plus_block_s < 60:
        return RunClassification.EASY_RECOVERY, 0.8, [f"{z1z2:.0f}% of moving time in Z1–Z2, no sustained Z4+ block"]

    return RunClassification.UNCLASSIFIED, 0.4, ["no rule matched cleanly — ask the user to confirm"]


def _detect_intervals(laps: list[LapRecord], min_bouts: int = 3) -> list[str] | None:
    if len(laps) < min_bouts * 2 - 1:
        return None
    durations = sorted(l.moving_time_s for l in laps)
    median = statistics.median(durations)
    short = [l for l in laps if l.moving_time_s <= median * 0.8]
    long_ = [l for l in laps if l.moving_time_s > median * 0.8]
    if len(short) >= min_bouts and len(long_) >= min_bouts - 1:
        return [f"{len(short)} laps cluster short (work bouts), {len(long_)} cluster long (recovery)"]
    return None


def _is_monotonic_decreasing(values: list[float], tolerance: float = 0.02) -> bool:
    thirds = _split_into_thirds(values)
    means = [statistics.mean(t) for t in thirds if t]
    if len(means) < 3:
        return False
    return means[0] > means[1] * (1 - tolerance) > means[2] * (1 - tolerance) * (1 - tolerance) or (
        means[0] > means[1] > means[2]
    )


def _split_into_thirds(values: list[float]) -> list[list[float]]:
    n = len(values)
    a = values[: n // 3] or [values[0]]
    b = values[n // 3 : 2 * n // 3] or [values[len(values) // 2]]
    c = values[2 * n // 3 :] or [values[-1]]
    return [a, b, c]


def build_load_context(
    acute_7d_load: float, chronic_28d_load: float, weekly_distance_km: float, zone1_2_pct_28d: float
) -> LoadContext:
    acwr = compute_acwr(acute_7d_load, chronic_28d_load) or 0.0
    return LoadContext(
        acute_7d_load=acute_7d_load,
        chronic_28d_load=chronic_28d_load,
        acwr=acwr,
        weekly_distance_km=weekly_distance_km,
        zone1_2_pct_28d=zone1_2_pct_28d,
        zone3_plus_pct_28d=100 - zone1_2_pct_28d,
    )
