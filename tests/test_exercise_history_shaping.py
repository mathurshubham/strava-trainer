"""Unit tests for the pure DB-row shaping used by chart providers — no live
Postgres. Covers the group-by-session + best_e1rm logic that get_e1rm_series
delegates to (the SQL+grouping helper), which fakes in the worker/dispatch
tests otherwise bypass."""
from datetime import datetime, timezone

from app.storage.exercise_history import e1rm_series_from_rows

D1 = datetime(2026, 7, 1, tzinfo=timezone.utc)
D2 = datetime(2026, 7, 8, tzinfo=timezone.utc)


def test_groups_rows_into_one_point_per_session():
    rows = [
        (D1, 100.0, 5, "normal"),
        (D1, 102.5, 3, "normal"),   # heavier top set same day -> higher e1RM
        (D2, 105.0, 5, "normal"),
    ]
    series = e1rm_series_from_rows(rows)
    assert len(series) == 2
    assert series[0][0] == D1 and series[1][0] == D2
    assert series[1][1] > series[0][1]  # ascending by date, distinct values


def test_warmups_excluded_and_valueless_sessions_dropped():
    rows = [
        (D1, 40.0, 10, "warmup"),   # only a warmup -> best_e1rm None -> session dropped
        (D2, 80.0, 5, "normal"),
    ]
    series = e1rm_series_from_rows(rows)
    assert len(series) == 1
    assert series[0][0] == D2


def test_empty_rows_give_empty_series():
    assert e1rm_series_from_rows([]) == []


def test_output_sorted_by_date_regardless_of_input_order():
    rows = [(D2, 105.0, 5, "normal"), (D1, 100.0, 5, "normal")]
    series = e1rm_series_from_rows(rows)
    assert [d for d, _ in series] == [D1, D2]
