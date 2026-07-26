"""Cross-training chart presets x01-x02 (PRD §6.2)."""
from __future__ import annotations

from datetime import date
from io import BytesIO

import numpy as np

from app.charts.theme import GRID, MUTED, color_for_index, finalize, new_figure


def x01_training_load_acwr(acute_series: list[tuple[date, float]], chronic_series: list[tuple[date, float]]) -> BytesIO:
    fig, ax = new_figure()
    ax.axhspan(0.8, 1.3, color="#4ff7a8", alpha=0.15, label="0.8-1.3 sweet spot")
    ax.axhline(1.5, color="#f76f4f", linestyle="--", linewidth=1, label="1.5 elevated risk")

    dates = [d for d, _ in acute_series]
    acwr = [
        a / (c / 4) if c else 0
        for (_, a), (_, c) in zip(acute_series, chronic_series)
    ]
    ax.plot(dates, acwr, color=color_for_index(0), linewidth=2, marker="o")
    fig.autofmt_xdate()
    ax.legend(facecolor="#1a1d24", edgecolor=GRID, labelcolor="#e8e9ec", fontsize=9)
    return finalize(fig, "Acute:chronic workload ratio", ylabel="ACWR")


_ACTIVITY_COLORS = {
    "run": "#4f8ff7",
    "ride": "#f7d24f",
    "weighttraining": "#f76f4f",
    "other": "#8b8f9a",
}


def x02_consistency_calendar(activities: list[tuple[date, str]], weeks: int = 12) -> BytesIO:
    fig, ax = new_figure()
    if not activities:
        return finalize(fig, "Consistency calendar")

    latest = max(d for d, _ in activities)
    grid = np.full((7, weeks), "", dtype=object)
    for d, sport in activities:
        week_offset = (latest - d).days // 7
        if 0 <= week_offset < weeks:
            grid[d.weekday(), weeks - 1 - week_offset] = sport.lower()

    for row in range(7):
        for col in range(weeks):
            sport = grid[row, col]
            if sport:
                ax.scatter(col, row, s=140, color=_ACTIVITY_COLORS.get(sport, _ACTIVITY_COLORS["other"]))

    ax.set_yticks(range(7))
    ax.set_yticklabels(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
    ax.set_xlabel("Weeks ago (right = most recent)")
    ax.set_xlim(-1, weeks)
    ax.set_ylim(-1, 7)
    ax.invert_yaxis()
    return finalize(fig, "Consistency calendar — all activity types")
