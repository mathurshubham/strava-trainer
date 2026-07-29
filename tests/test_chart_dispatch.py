"""Dispatcher logic: chart_id -> data provider -> renderer, and the subject
resolution rules. Pure — no DB, no LLM (mirrors the house rule that everything
stays unit-testable with fakes). The repo's SQL and the renderers themselves are
covered elsewhere (a live Postgres / test_charts.py); here we exercise the
routing, the thin-history / unwired-id fallbacks, and the strict focus rule.
"""
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace

from app.agents.chart_dispatch import has_exercise_context, render_selected_chart
from app.models.agent_io import ChartParam, ChartSelection, OrchestratorOutput, TargetEntity
from app.models.enums import Intent

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class FakeHistory:
    def __init__(self, **kw):
        self.e1rm_series_by_key = kw.get("e1rm_series_by_key", {})
        self.volume_series = kw.get("volume_series", [])
        self.pr_timeline = kw.get("pr_timeline", [])
        self.session_dates = kw.get("session_dates", [])

    def get_e1rm_series(self, key):
        return self.e1rm_series_by_key.get(key, [])

    def get_volume_series(self):
        return self.volume_series

    def get_pr_timeline(self):
        return self.pr_timeline

    def get_session_dates(self):
        return self.session_dates


def _route(target=None):
    return OrchestratorOutput(
        intent=Intent.QUESTION_ABOUT_WORKOUT, target_entity=target,
        needs_data=["exercise_history"], needs_chart=True, resolved_pronouns=[], route_to="chart",
    )


def _focus(type_, ref):
    return SimpleNamespace(focus=SimpleNamespace(type=type_, ref=ref))


def _assert_png(buf):
    assert isinstance(buf, BytesIO)
    data = buf.read()
    assert data.startswith(PNG_MAGIC) and len(data) > 500


# --- subject resolution ------------------------------------------------------

def test_exercise_context_from_target_entity():
    assert has_exercise_context(_route(TargetEntity(type="exercise", ref="bench press")), None) is True


def test_exercise_context_from_focus_only_when_type_is_exercise():
    assert has_exercise_context(_route(None), _focus("exercise", "bench press")) is True
    # A fresh post-briefing focus is a workout/activity id, NOT an exercise.
    assert has_exercise_context(_route(None), _focus("workout", "123456789")) is False
    assert has_exercise_context(_route(None), _focus("activity", "987654321")) is False


# --- dispatch ----------------------------------------------------------------

def test_e1rm_trend_renders_from_focus_subject():
    hist = FakeHistory(e1rm_series_by_key={"bench_press": [
        (datetime(2026, 7, 1, tzinfo=timezone.utc), 100.0),
        (datetime(2026, 7, 8, tzinfo=timezone.utc), 105.0),
    ]})
    deps = SimpleNamespace(exercise_history=hist)
    selection = ChartSelection(chart_id="s03_e1rm_trend", params=[], caption="c")
    png = render_selected_chart(selection, _route(None), _focus("exercise", "Bench Press (Barbell)"), deps)
    _assert_png(png)


def test_e1rm_trend_none_without_a_subject():
    deps = SimpleNamespace(exercise_history=FakeHistory())
    selection = ChartSelection(chart_id="s03_e1rm_trend", params=[], caption="c")
    assert render_selected_chart(selection, _route(None), _focus("workout", "123"), deps) is None


def test_e1rm_trend_uses_param_subject():
    hist = FakeHistory(e1rm_series_by_key={"squat": [
        (datetime(2026, 7, 1, tzinfo=timezone.utc), 140.0),
        (datetime(2026, 7, 8, tzinfo=timezone.utc), 145.0),
    ]})
    deps = SimpleNamespace(exercise_history=hist)
    selection = ChartSelection(chart_id="s03_e1rm_trend", params=[ChartParam(key="exercise", value="Squat (Barbell)")], caption="c")
    _assert_png(render_selected_chart(selection, _route(None), None, deps))


def test_thin_history_returns_none():
    hist = FakeHistory(e1rm_series_by_key={"bench_press": [(datetime(2026, 7, 1, tzinfo=timezone.utc), 100.0)]})
    deps = SimpleNamespace(exercise_history=hist)
    selection = ChartSelection(chart_id="s03_e1rm_trend", params=[], caption="c")
    assert render_selected_chart(selection, _route(TargetEntity(type="exercise", ref="bench press")), None, deps) is None


def test_unwired_chart_id_returns_none():
    deps = SimpleNamespace(exercise_history=FakeHistory())
    selection = ChartSelection(chart_id="e06_run_overlay", params=[], caption="c")
    assert render_selected_chart(selection, _route(None), None, deps) is None


def test_volume_trend_renders():
    hist = FakeHistory(volume_series=[
        (datetime(2026, 7, 1, tzinfo=timezone.utc), 5000.0),
        (datetime(2026, 7, 8, tzinfo=timezone.utc), 5200.0),
    ])
    deps = SimpleNamespace(exercise_history=hist)
    _assert_png(render_selected_chart(ChartSelection(chart_id="s02_volume_trend", params=[], caption="c"), _route(None), None, deps))


def test_pr_timeline_renders():
    hist = FakeHistory(pr_timeline=[
        (datetime(2026, 7, 1, tzinfo=timezone.utc), "Bench Press", 100.0),
        (datetime(2026, 7, 8, tzinfo=timezone.utc), "Squat", 140.0),
    ])
    deps = SimpleNamespace(exercise_history=hist)
    _assert_png(render_selected_chart(ChartSelection(chart_id="s08_pr_timeline", params=[], caption="c"), _route(None), None, deps))


def test_frequency_heatmap_renders():
    hist = FakeHistory(session_dates=[datetime(2026, 7, d, tzinfo=timezone.utc).date() for d in (1, 3, 8, 10)])
    deps = SimpleNamespace(exercise_history=hist)
    _assert_png(render_selected_chart(ChartSelection(chart_id="s12_frequency_heatmap", params=[], caption="c"), _route(None), None, deps))


def test_frequency_heatmap_none_when_empty():
    deps = SimpleNamespace(exercise_history=FakeHistory(session_dates=[]))
    assert render_selected_chart(ChartSelection(chart_id="s12_frequency_heatmap", params=[], caption="c"), _route(None), None, deps) is None
