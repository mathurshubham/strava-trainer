"""Shared chart styling. One place for palette/fonts/gridlines so all 22
presets read as one system. PRD §6.1: 1200x675 @ dpi100, dark by default
(matches Telegram's default theme), Agg backend, in-memory only — never
write to disk on serverless.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

from io import BytesIO

import matplotlib.pyplot as plt
from matplotlib.figure import Figure

FIG_WIDTH_IN = 12.0
FIG_HEIGHT_IN = 6.75
DPI = 100

BG = "#111318"
PANEL_BG = "#1a1d24"
FG = "#e8e9ec"
GRID = "#33363f"
MUTED = "#8b8f9a"

PALETTE = [
    "#4f8ff7",  # blue
    "#f76f4f",  # orange
    "#4ff7a8",  # green
    "#f7d24f",  # yellow
    "#c04ff7",  # purple
    "#4ff7ec",  # cyan
    "#f74f8f",  # pink
    "#9ef74f",  # lime
]

ZONE_COLORS = {
    "Z1 Recovery": "#4f8ff7",
    "Z2 Aerobic": "#4ff7a8",
    "Z3 Tempo": "#f7d24f",
    "Z4 Threshold": "#f76f4f",
    "Z5 VO2max": "#f74f4f",
}


def new_figure(*, rows: int = 1, cols: int = 1) -> tuple[Figure, "plt.Axes | list"]:
    fig, ax = plt.subplots(
        rows, cols, figsize=(FIG_WIDTH_IN, FIG_HEIGHT_IN), dpi=DPI, facecolor=BG
    )
    axes = ax if isinstance(ax, (list, tuple)) or hasattr(ax, "__len__") else [ax]
    for a in (axes if hasattr(axes, "__iter__") else [axes]):
        a.set_facecolor(PANEL_BG)
        a.tick_params(colors=FG, labelsize=9)
        a.xaxis.label.set_color(FG)
        a.yaxis.label.set_color(FG)
        a.title.set_color(FG)
        for spine in a.spines.values():
            spine.set_color(GRID)
        a.grid(True, color=GRID, linewidth=0.6, alpha=0.7)
    return fig, ax


def style_legend(ax, **kwargs) -> None:
    legend = ax.legend(facecolor=PANEL_BG, edgecolor=GRID, labelcolor=FG, fontsize=9, **kwargs)
    if legend:
        legend.get_frame().set_alpha(0.9)


def finalize(fig: Figure, title: str, *, xlabel: str = "", ylabel: str = "") -> BytesIO:
    axes = fig.get_axes()
    if axes:
        axes[0].set_title(title, fontsize=13, fontweight="bold", pad=12)
        if xlabel:
            axes[0].set_xlabel(xlabel)
        if ylabel:
            axes[0].set_ylabel(ylabel)
    fig.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


def color_for_index(i: int) -> str:
    return PALETTE[i % len(PALETTE)]
