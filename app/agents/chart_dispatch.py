"""Turns a ChartSelection into a rendered PNG, if the selected chart_id is one
of the v1-wired ones and there is enough history to draw it.

The ChartAgent only *picks* a chart_id (from VALID_CHART_IDS) and writes a
caption — it never fetches data. This module owns the id -> (data fetch +
renderer) mapping. All SQL lives in the repo (ExerciseHistoryRepo); no
arithmetic happens here — providers only shape already-computed rows and hand
them to a pure renderer.

v1 wires four ids (s02/s03/s08/s12). Every other id returns None so the caller
falls back to a plain text reply — "I can't chart that one yet" — rather than
crashing or sending an empty image.
"""
from __future__ import annotations

import logging
from io import BytesIO
from typing import TYPE_CHECKING

from app.charts import strength as strength_charts
from app.metrics.muscle_mapping import normalize_exercise_name
from app.models.agent_io import ChartSelection, OrchestratorOutput

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.session import SessionState

logger = logging.getLogger(__name__)

_MIN_TREND_POINTS = 2


def _exercise_subject(selection: ChartSelection, route: OrchestratorOutput, state: SessionState | None) -> str | None:
    """The exercise a chart is about, as a normalized template key, or None.

    Precedence: an explicit exercise target_entity, then a chart param, then the
    session focus. The focus type check MUST stay strict `== "exercise"`: a
    strength briefing sets focus.type == "workout" with ref == <activity_id> and
    endurance sets "activity" — feeding that ref to normalize_exercise_name would
    produce a garbage key and an empty query. Focus only becomes "exercise" once
    the conversation narrows to a lift.
    """
    if route.target_entity is not None and route.target_entity.type == "exercise" and route.target_entity.ref:
        return normalize_exercise_name(route.target_entity.ref)
    for param in selection.params:
        if param.key in ("exercise", "exercise_name", "ref") and param.value:
            return normalize_exercise_name(param.value)
    if state is not None and state.focus is not None and state.focus.type == "exercise" and state.focus.ref:
        return normalize_exercise_name(state.focus.ref)
    return None


def has_exercise_context(route: OrchestratorOutput, state: SessionState | None) -> bool:
    """Whether a single exercise is the subject, from route or session focus.

    Used before a ChartSelection exists to decide which chart_ids to offer the
    agent. Same strict focus.type == "exercise" rule as _exercise_subject.
    """
    if route.target_entity is not None and route.target_entity.type == "exercise" and route.target_entity.ref:
        return True
    return state is not None and state.focus is not None and state.focus.type == "exercise" and bool(state.focus.ref)


def _render_e1rm_trend(selection, route, state, deps) -> BytesIO | None:
    key = _exercise_subject(selection, route, state)
    if key is None:
        return None
    points = deps.exercise_history.get_e1rm_series(key)
    if len(points) < _MIN_TREND_POINTS:
        return None
    label = (route.target_entity.ref if route.target_entity else None) or key
    return strength_charts.s03_e1rm_trend(label, points)


def _render_volume_trend(selection, route, state, deps) -> BytesIO | None:
    sessions = deps.exercise_history.get_volume_series()
    if len(sessions) < _MIN_TREND_POINTS:
        return None
    return strength_charts.s02_volume_trend(sessions)


def _render_pr_timeline(selection, route, state, deps) -> BytesIO | None:
    prs = deps.exercise_history.get_pr_timeline()
    if len(prs) < _MIN_TREND_POINTS:
        return None
    return strength_charts.s08_pr_timeline(prs)


def _render_frequency_heatmap(selection, route, state, deps) -> BytesIO | None:
    dates = deps.exercise_history.get_session_dates()
    if not dates:
        return None
    return strength_charts.s12_frequency_heatmap(dates)


_PROVIDERS = {
    "s03_e1rm_trend": _render_e1rm_trend,
    "s02_volume_trend": _render_volume_trend,
    "s08_pr_timeline": _render_pr_timeline,
    "s12_frequency_heatmap": _render_frequency_heatmap,
}


def render_selected_chart(
    selection: ChartSelection, route: OrchestratorOutput, state: SessionState | None, deps
) -> BytesIO | None:
    """Render the PNG for selection.chart_id, or None to fall back to text.

    Returns None when the chart_id has no v1 data provider, or when history is
    too thin to draw a meaningful chart. Never raises for either case.
    """
    provider = _PROVIDERS.get(selection.chart_id)
    if provider is None:
        logger.info("chart_dispatch: no v1 provider for chart_id=%s; falling back to text", selection.chart_id)
        return None
    return provider(selection, route, state, deps)
