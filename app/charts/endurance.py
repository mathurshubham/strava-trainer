"""Endurance chart presets e01-e10 (PRD §6.2)."""
from __future__ import annotations

from datetime import date, datetime
from io import BytesIO

import numpy as np

from app.charts.theme import GRID, MUTED, ZONE_COLORS, color_for_index, finalize, new_figure
from app.models.streams import StreamSample
from app.models.workout import SplitRecord, ZoneTime


def e01_hr_zone_distribution(zone_times: list[ZoneTime]) -> BytesIO:
    fig, ax = new_figure()
    left = 0
    for z in zone_times:
        minutes = z.seconds / 60
        ax.barh(0, minutes, left=left, color=ZONE_COLORS.get(z.zone, MUTED), label=f"{z.zone} ({z.pct_of_moving_time:.0f}%)")
        left += minutes
    ax.set_yticks([])
    ax.legend(facecolor="#1a1d24", edgecolor=GRID, labelcolor="#e8e9ec", fontsize=9, loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=3)
    return finalize(fig, "Time in HR zone", xlabel="Minutes")


def e02_hr_over_time_zoned(samples: list[StreamSample], boundaries: list[tuple[str, float, float]]) -> BytesIO:
    fig, ax = new_figure()
    for name, lo, hi in boundaries:
        ax.axhspan(lo, hi, color=ZONE_COLORS.get(name, MUTED), alpha=0.15)
    times = [s.time_s / 60 for s in samples if s.heartrate is not None]
    hrs = [s.heartrate for s in samples if s.heartrate is not None]
    ax.plot(times, hrs, color="#e8e9ec", linewidth=1.5)
    return finalize(fig, "Heart rate over time", xlabel="Minutes", ylabel="BPM")


def e03_pace_hr_dual(samples: list[StreamSample]) -> BytesIO:
    fig, ax1 = new_figure()
    ordered = sorted((s for s in samples if s.distance_m is not None), key=lambda s: s.time_s)
    dist_km = [s.distance_m / 1000 for s in ordered]
    paces = []
    for cur, nxt in zip(ordered, ordered[1:]):
        d = nxt.distance_m - cur.distance_m
        t = nxt.time_s - cur.time_s
        paces.append((t / d * 1000) / 60 if d > 0 else None)
    hrs = [s.heartrate for s in ordered]

    ax1.plot(dist_km[: len(paces)], paces, color=color_for_index(0), label="Pace (min/km)")
    ax1.set_ylabel("Pace (min/km)")
    ax1.invert_yaxis()

    ax2 = ax1.twinx()
    ax2.plot(dist_km, hrs, color=color_for_index(1), label="HR", alpha=0.8)
    ax2.set_ylabel("BPM")
    ax2.tick_params(colors="#e8e9ec")

    return finalize(fig, "Pace and heart rate vs distance", xlabel="Distance (km)")


def e04_splits_bar(splits: list[SplitRecord]) -> BytesIO:
    fig, ax = new_figure()
    paces = [s.pace_s_per_km for s in splits]
    avg = sum(paces) / len(paces) if paces else 0
    colors = [color_for_index(0) if p <= avg else "#f76f4f" for p in paces]
    x = np.arange(len(splits))
    ax.bar(x, paces, color=colors)
    ax.axhline(avg, color=MUTED, linestyle="--", linewidth=1, label="Average")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{s.split_index}" for s in splits])
    ax.invert_yaxis()
    ax.legend(facecolor="#1a1d24", edgecolor=GRID, labelcolor="#e8e9ec", fontsize=9)
    return finalize(fig, "Splits", xlabel="Km", ylabel="Pace (s/km)")


def e05_decoupling(first_half_ef: float, second_half_ef: float) -> BytesIO:
    fig, ax = new_figure()
    ax.bar(["First half", "Second half"], [first_half_ef, second_half_ef], color=[color_for_index(0), color_for_index(1)])
    return finalize(fig, "Decoupling (Pa:HR)", ylabel="Efficiency factor")


def e06_run_overlay(series_a: list[tuple[float, float]], series_b: list[tuple[float, float]], label_a: str, label_b: str, ylabel: str) -> BytesIO:
    fig, ax = new_figure()
    ax.plot([d for d, _ in series_a], [v for _, v in series_a], color=color_for_index(0), label=label_a)
    ax.plot([d for d, _ in series_b], [v for _, v in series_b], color=color_for_index(1), label=label_b)
    ax.legend(facecolor="#1a1d24", edgecolor=GRID, labelcolor="#e8e9ec", fontsize=9)
    return finalize(fig, "Run comparison", xlabel="Distance (km)", ylabel=ylabel)


def e07_weekly_volume_zones(weekly: dict[str, dict[str, float]]) -> BytesIO:
    fig, ax = new_figure()
    weeks = list(weekly.keys())
    zone_names = sorted({z for wk in weekly.values() for z in wk})
    x = np.arange(len(weeks))
    bottom = np.zeros(len(weeks))
    for zone in zone_names:
        values = np.array([weekly[w].get(zone, 0) for w in weeks])
        ax.bar(x, values, bottom=bottom, color=ZONE_COLORS.get(zone, MUTED), label=zone)
        bottom += values
    ax.set_xticks(x)
    ax.set_xticklabels(weeks, rotation=20, ha="right")
    ax.legend(facecolor="#1a1d24", edgecolor=GRID, labelcolor="#e8e9ec", fontsize=9)
    return finalize(fig, "Weekly volume by zone", ylabel="Distance (km)")


def e08_zone_mix_28d(zone1_2_pct: float, zone3_plus_pct: float) -> BytesIO:
    fig, ax = new_figure()
    wedges, _ = ax.pie(
        [zone1_2_pct, zone3_plus_pct],
        colors=[color_for_index(2), color_for_index(1)],
        startangle=90,
        wedgeprops={"width": 0.4, "edgecolor": "#111318"},
    )
    ax.legend(wedges, [f"Z1-Z2 ({zone1_2_pct:.0f}%)", f"Z3+ ({zone3_plus_pct:.0f}%)"], facecolor="#1a1d24", edgecolor=GRID, labelcolor="#e8e9ec", fontsize=9, loc="center")
    return finalize(fig, "80/20 zone mix — trailing 28 days")


def e09_cadence_over_time(samples: list[tuple[float, float]], target_lo: float = 170, target_hi: float = 185) -> BytesIO:
    fig, ax = new_figure()
    ax.axhspan(target_lo, target_hi, color="#4ff7a8", alpha=0.15, label="Target band")
    times = [t / 60 for t, _ in samples]
    cadences = [c for _, c in samples]
    ax.plot(times, cadences, color=color_for_index(0), linewidth=1.5)
    ax.legend(facecolor="#1a1d24", edgecolor=GRID, labelcolor="#e8e9ec", fontsize=9)
    return finalize(fig, "Cadence over time", xlabel="Minutes", ylabel="Steps/min")


def e10_elevation_hr(samples: list[StreamSample]) -> BytesIO:
    fig, ax1 = new_figure()
    ordered = sorted((s for s in samples if s.distance_m is not None), key=lambda s: s.time_s)
    dist_km = [s.distance_m / 1000 for s in ordered]
    elevations = [s.altitude_m or 0 for s in ordered]
    hrs = [s.heartrate for s in ordered]

    ax1.fill_between(dist_km, elevations, color=color_for_index(3), alpha=0.3)
    ax1.plot(dist_km, elevations, color=color_for_index(3))
    ax1.set_ylabel("Elevation (m)")

    ax2 = ax1.twinx()
    ax2.plot(dist_km, hrs, color=color_for_index(1))
    ax2.set_ylabel("BPM")
    ax2.tick_params(colors="#e8e9ec")

    return finalize(fig, "Elevation profile with heart rate", xlabel="Distance (km)")
