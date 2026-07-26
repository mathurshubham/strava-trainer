"""Strength chart presets s01-s12 (PRD §6.2). Each takes plain, already-computed
data (never raw API JSON) and returns a PNG in a BytesIO. The Chart Agent LLM
only ever picks a `chart_id` + simple params — it never writes plotting code.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from io import BytesIO

import numpy as np

from app.charts.theme import GRID, MUTED, color_for_index, finalize, new_figure
from app.models.enums import SetType
from app.models.workout import MuscleGroupVolume, SetRecord


def s01_volume_by_muscle(muscle_volumes: list[MuscleGroupVolume]) -> BytesIO:
    fig, ax = new_figure()
    groups = [m.muscle_group for m in muscle_volumes]
    volumes = [m.volume_kg for m in muscle_volumes]
    y = np.arange(len(groups))
    ax.barh(y, volumes, color=[color_for_index(i) for i in range(len(groups))])
    ax.set_yticks(y)
    ax.set_yticklabels(groups)
    ax.invert_yaxis()
    return finalize(fig, "Volume by muscle group", xlabel="Volume (kg)")


def s02_volume_trend(sessions: list[tuple[datetime, float]]) -> BytesIO:
    fig, ax = new_figure()
    dates = [d for d, _ in sessions]
    volumes = [v for _, v in sessions]
    ax.plot(dates, volumes, marker="o", color=color_for_index(0), linewidth=2)
    fig.autofmt_xdate()
    return finalize(fig, "Total volume per session", ylabel="Volume (kg)")


def s03_e1rm_trend(exercise_name: str, points: list[tuple[datetime, float]]) -> BytesIO:
    fig, ax = new_figure()
    dates = [d for d, _ in points]
    values = [v for _, v in points]
    ax.plot(dates, values, marker="o", color=color_for_index(1), linewidth=2)
    fig.autofmt_xdate()
    return finalize(fig, f"Estimated 1RM — {exercise_name}", ylabel="e1RM (kg)")


def s04_set_breakdown(exercise_name: str, sets: list[SetRecord]) -> BytesIO:
    fig, ax = new_figure()
    x = np.arange(len(sets))
    colors = [MUTED if s.set_type == SetType.WARMUP else color_for_index(0) for s in sets]
    bars = ax.bar(x, [s.weight_kg for s in sets], color=colors)
    for bar, s in zip(bars, sets):
        ax.annotate(f"{s.reps}", (bar.get_x() + bar.get_width() / 2, bar.get_height()), ha="center", va="bottom", color="#e8e9ec", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels([f"Set {s.set_index + 1}" for s in sets])
    return finalize(fig, f"Set breakdown — {exercise_name}", ylabel="Weight (kg)")


def s05_session_vs_last(current: dict[str, float], previous: dict[str, float]) -> BytesIO:
    fig, ax = new_figure()
    exercises = list(current.keys())
    x = np.arange(len(exercises))
    width = 0.35
    ax.bar(x - width / 2, [previous.get(e, 0) for e in exercises], width, label="Last time", color=MUTED)
    ax.bar(x + width / 2, [current[e] for e in exercises], width, label="Today", color=color_for_index(0))
    ax.set_xticks(x)
    ax.set_xticklabels(exercises, rotation=20, ha="right")
    ax.legend(facecolor="#1a1d24", edgecolor=GRID, labelcolor="#e8e9ec", fontsize=9)
    return finalize(fig, "This session vs last time", ylabel="Volume (kg)")


def s06_weekly_sets_vs_target(weekly_sets: dict[str, float], target_lo: float = 10, target_hi: float = 20) -> BytesIO:
    fig, ax = new_figure()
    groups = list(weekly_sets.keys())
    values = [weekly_sets[g] for g in groups]
    y = np.arange(len(groups))
    ax.axvspan(target_lo, target_hi, color="#4ff7a8", alpha=0.15, label=f"{target_lo:g}-{target_hi:g} set band")
    ax.barh(y, values, color=[color_for_index(i) for i in range(len(groups))])
    ax.set_yticks(y)
    ax.set_yticklabels(groups)
    ax.invert_yaxis()
    ax.legend(facecolor="#1a1d24", edgecolor=GRID, labelcolor="#e8e9ec", fontsize=9)
    return finalize(fig, "Weekly working sets vs evidence-based target", xlabel="Sets")


def s07_rep_range_mix(counts: dict[str, int]) -> BytesIO:
    fig, ax = new_figure()
    labels = list(counts.keys())
    values = [counts[l] for l in labels]
    left = 0
    for i, (label, value) in enumerate(zip(labels, values)):
        ax.barh(0, value, left=left, color=color_for_index(i), label=label)
        left += value
    ax.set_yticks([])
    ax.legend(facecolor="#1a1d24", edgecolor=GRID, labelcolor="#e8e9ec", fontsize=9, loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=len(labels))
    return finalize(fig, "Rep-range mix", xlabel="Sets")


def s08_pr_timeline(prs: list[tuple[datetime, str, float]]) -> BytesIO:
    fig, ax = new_figure()
    exercises = sorted({name for _, name, _ in prs})
    y_for = {name: i for i, name in enumerate(exercises)}
    for i, (dt, name, value) in enumerate(prs):
        ax.scatter(dt, y_for[name], s=80, color=color_for_index(y_for[name]))
        ax.annotate(f"{value:g}kg", (dt, y_for[name]), textcoords="offset points", xytext=(6, 4), fontsize=8, color="#e8e9ec")
    ax.set_yticks(list(y_for.values()))
    ax.set_yticklabels(list(y_for.keys()))
    fig.autofmt_xdate()
    return finalize(fig, "PR timeline")


def s09_load_reps_scatter(sets: list[tuple[float, int]], exercise_name: str) -> BytesIO:
    fig, ax = new_figure()
    weights = [w for w, _ in sets]
    reps = [r for _, r in sets]
    ax.scatter(reps, weights, s=60, color=color_for_index(0), zorder=3)

    if weights:
        max_e1rm = max(w * (1 + r / 30) for w, r in sets)
        rep_range = np.linspace(1, 12, 50)
        for frac in (0.7, 0.85, 1.0):
            iso_weight = (max_e1rm * frac) / (1 + rep_range / 30)
            ax.plot(rep_range, iso_weight, linestyle="--", linewidth=1, color=MUTED, alpha=0.6)

    return finalize(fig, f"Load x reps — {exercise_name}", xlabel="Reps", ylabel="Weight (kg)")


def s10_muscle_balance_radar(balance: dict[str, float]) -> BytesIO:
    labels = list(balance.keys())
    values = [balance[l] for l in labels]
    angles = np.linspace(0, 2 * math.pi, len(labels), endpoint=False).tolist()
    values += values[:1]
    angles += angles[:1]

    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(12.0, 6.75), dpi=100, facecolor="#111318")
    ax = fig.add_subplot(111, polar=True, facecolor="#1a1d24")
    ax.plot(angles, values, color=color_for_index(0), linewidth=2)
    ax.fill(angles, values, color=color_for_index(0), alpha=0.25)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, color="#e8e9ec")
    ax.tick_params(colors="#e8e9ec")
    return finalize(fig, "Muscle balance")


def s11_intensity_distribution(pct_of_e1rm: list[float]) -> BytesIO:
    fig, ax = new_figure()
    ax.hist(pct_of_e1rm, bins=10, range=(0, 120), color=color_for_index(0), edgecolor="#111318")
    return finalize(fig, "Intensity distribution (% of e1RM per set)", xlabel="% of e1RM", ylabel="Sets")


def s12_frequency_heatmap(session_dates: list[date], weeks: int = 12) -> BytesIO:
    fig, ax = new_figure()
    if not session_dates:
        return finalize(fig, "Session frequency", xlabel="Week", ylabel="Day")

    latest = max(session_dates)
    grid = np.zeros((7, weeks))
    for d in session_dates:
        week_offset = (latest - d).days // 7
        if 0 <= week_offset < weeks:
            grid[d.weekday(), weeks - 1 - week_offset] = 1

    ax.imshow(grid, aspect="auto", cmap="Greens", vmin=0, vmax=1)
    ax.set_yticks(range(7))
    ax.set_yticklabels(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
    ax.set_xlabel("Weeks ago (right = most recent)")
    return finalize(fig, "Session frequency")
