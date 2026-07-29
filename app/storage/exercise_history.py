"""Postgres-backed history lookups for the strength metrics engine, and the
write path that persists a computed session. Since lifting data comes from
the Strava description (not the Hevy API), this table set — not a Hevy
endpoint — is what answers "what did I lift last time" (PRD §3.5 alternative,
as decided in DECISIONS.md: Strava is the only lifting data source).

Kept deliberately separate from app/metrics/strength.py: the metrics module
stays pure and DB-free so it's unit-testable with plain fixtures; this module
is the (harder to unit-test without a live Postgres) glue that fetches what
that module needs.
"""
from __future__ import annotations

from datetime import date, datetime

from app.metrics.muscle_mapping import normalize_exercise_name
from app.metrics.strength import best_e1rm, build_set_record
from app.models.enums import SetType
from app.models.history import ExerciseBests, PreviousOccurrence, SessionBests
from app.models.parsed import RawParsedSet
from app.models.workout import EnduranceWorkoutContext, StrengthWorkoutContext
from app.storage.db import Database

SESSION_PR_KEY = "__session__"


def e1rm_series_from_rows(rows: list[tuple]) -> list[tuple[datetime, float]]:
    """Pure shaping for get_e1rm_series: `(start_date, weight_kg, reps, set_type)`
    rows (already ordered by date, set_index) -> one `(start_date, best_e1rm)`
    point per session that has one. Kept DB-free so it's unit-testable without a
    live Postgres; the e1RM math reuses the pure metrics helpers (best_e1rm
    filters warmups internally)."""
    by_session: dict[datetime, list[RawParsedSet]] = {}
    for start_date, weight_kg, reps, set_type in rows:
        by_session.setdefault(start_date, []).append(
            RawParsedSet(weight_kg=weight_kg, reps=reps, set_type=SetType(set_type))
        )

    series: list[tuple[datetime, float]] = []
    for start_date in sorted(by_session):
        records = [build_set_record(s, i) for i, s in enumerate(by_session[start_date])]
        value = best_e1rm(records)
        if value is not None:
            series.append((start_date, value))
    return series


class ExerciseHistoryRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get_previous_occurrence(self, exercise_key: str, before: datetime) -> PreviousOccurrence | None:
        with self._db.connection() as conn:
            row = conn.execute(
                """
                SELECT a.strava_id, a.start_date
                FROM exercise_sets es JOIN activities a ON a.strava_id = es.strava_id
                WHERE es.exercise_template_key = %s AND a.start_date < %s
                ORDER BY a.start_date DESC LIMIT 1
                """,
                (exercise_key, before),
            ).fetchone()
            if row is None:
                return None
            strava_id, start_date = row

            set_rows = conn.execute(
                """
                SELECT weight_kg, reps, set_type FROM exercise_sets
                WHERE strava_id = %s AND exercise_template_key = %s AND set_type != 'warmup'
                ORDER BY set_index
                """,
                (strava_id, exercise_key),
            ).fetchall()

        sets = [RawParsedSet(weight_kg=w, reps=r, set_type=SetType(t)) for w, r, t in set_rows]
        return PreviousOccurrence(date=start_date, sets=sets)

    def get_exercise_bests(self, exercise_key: str) -> ExerciseBests:
        with self._db.connection() as conn:
            pr_rows = conn.execute(
                "SELECT record_type, MAX(value) FROM personal_records WHERE exercise_template_key = %s GROUP BY record_type",
                (exercise_key,),
            ).fetchall()
            reps_rows = conn.execute(
                """
                SELECT weight_kg, MAX(reps) FROM exercise_sets
                WHERE exercise_template_key = %s AND set_type != 'warmup'
                GROUP BY weight_kg
                """,
                (exercise_key,),
            ).fetchall()

        by_type = {record_type: value for record_type, value in pr_rows}
        return ExerciseBests(
            heaviest_weight_kg=by_type.get("heaviest_weight"),
            best_e1rm_kg=by_type.get("best_e1rm"),
            highest_exercise_volume_kg=by_type.get("highest_exercise_volume"),
            max_reps_at_weight={round(weight, 2): reps for weight, reps in reps_rows},
        )

    def get_most_recent_activity_id(self) -> int | None:
        """Backs the /last command — re-fetches and re-briefs the most recent
        logged activity regardless of sport type."""
        with self._db.connection() as conn:
            row = conn.execute("SELECT strava_id FROM activities ORDER BY start_date DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def get_session_bests(self) -> SessionBests:
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT MAX(value) FROM personal_records WHERE exercise_template_key = %s AND record_type = 'highest_session_volume'",
                (SESSION_PR_KEY,),
            ).fetchone()
        return SessionBests(highest_session_volume_kg=row[0] if row else None)

    def get_e1rm_series(self, exercise_key: str) -> list[tuple[datetime, float]]:
        """Per-session best estimated 1RM for one exercise, over time — backs the
        `s03_e1rm_trend` chart. Mirrors get_previous_occurrence's set fetch, but
        across every session; the e1RM arithmetic reuses the pure metrics helpers
        (best_e1rm filters warmups internally), so no math happens in SQL here."""
        with self._db.connection() as conn:
            rows = conn.execute(
                """
                SELECT a.start_date, es.weight_kg, es.reps, es.set_type
                FROM exercise_sets es JOIN activities a ON a.strava_id = es.strava_id
                WHERE es.exercise_template_key = %s
                ORDER BY a.start_date, es.set_index
                """,
                (exercise_key,),
            ).fetchall()
        return e1rm_series_from_rows(rows)

    def get_volume_series(self) -> list[tuple[datetime, float]]:
        """Per-session total working volume over time — backs `s02_volume_trend`.
        The total_volume_kg metric row is only written for strength sessions, so
        this series is lifting-only (endurance activities carry no volume)."""
        with self._db.connection() as conn:
            rows = conn.execute(
                """
                SELECT a.start_date, m.metric_value
                FROM metrics m JOIN activities a ON a.strava_id = m.source_id
                WHERE m.metric_key = 'total_volume_kg' AND m.source_type = 'activity'
                ORDER BY a.start_date
                """,
            ).fetchall()
        return [(start_date, float(value)) for start_date, value in rows]

    def get_pr_timeline(self) -> list[tuple[datetime, str, float]]:
        """(date, display-name, value) for weight/e1RM PRs — backs `s08_pr_timeline`.
        The exercise name is pulled via a scalar subquery (a plain join to
        exercise_sets would multiply one PR into many set rows). The record_type
        filter already excludes the __session__ synthetic key."""
        with self._db.connection() as conn:
            rows = conn.execute(
                """
                SELECT pr.achieved_at,
                       (SELECT exercise_name FROM exercise_sets
                        WHERE exercise_template_key = pr.exercise_template_key LIMIT 1) AS label,
                       pr.value
                FROM personal_records pr
                WHERE pr.record_type IN ('heaviest_weight', 'best_e1rm')
                ORDER BY pr.achieved_at
                """,
            ).fetchall()
        return [(achieved_at, label or "exercise", float(value)) for achieved_at, label, value in rows]

    def get_session_dates(self) -> list[date]:
        """Dates of logged weight-training sessions — backs `s12_frequency_heatmap`."""
        with self._db.connection() as conn:
            rows = conn.execute(
                "SELECT start_date::date FROM activities WHERE sport_type = 'WeightTraining' ORDER BY start_date",
            ).fetchall()
        return [row[0] for row in rows]

    def record_strength_session(self, *, activity_raw_json: dict, context: StrengthWorkoutContext) -> None:
        with self._db.connection() as conn:
            conn.execute(
                """
                INSERT INTO activities (strava_id, sport_type, title, start_date, duration_s, raw_json)
                VALUES (%s, 'WeightTraining', %s, %s, %s, %s)
                ON CONFLICT (strava_id) DO UPDATE SET
                    title = EXCLUDED.title, raw_json = EXCLUDED.raw_json
                """,
                (context.strava_activity_id, context.title, context.start_time, context.duration_s, psycopg_json(activity_raw_json)),
            )

            for exercise in context.exercises:
                key = normalize_exercise_name(exercise.exercise_name)
                for s in exercise.sets:
                    conn.execute(
                        """
                        INSERT INTO exercise_sets
                            (strava_id, exercise_name, exercise_template_key, set_index, set_type, weight_kg, reps, rpe, rest_seconds)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            context.strava_activity_id, exercise.exercise_name, key, s.set_index, s.set_type.value,
                            s.weight_kg, s.reps, s.rpe, s.rest_seconds,
                        ),
                    )

            for pr in context.personal_records:
                key = SESSION_PR_KEY if pr.exercise_name == SESSION_PR_KEY else normalize_exercise_name(pr.exercise_name)
                conn.execute(
                    """
                    INSERT INTO personal_records (exercise_template_key, record_type, value, achieved_at, source_id)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (key, pr.record_type.value, pr.value, pr.achieved_at, context.strava_activity_id),
                )

            conn.execute(
                "INSERT INTO metrics (source_id, source_type, metric_key, metric_value) VALUES (%s, 'activity', 'total_volume_kg', %s)",
                (context.strava_activity_id, context.total_volume_kg),
            )
            for mv in context.volume_by_muscle_group:
                conn.execute(
                    "INSERT INTO metrics (source_id, source_type, metric_key, metric_value) VALUES (%s, 'activity', %s, %s)",
                    (context.strava_activity_id, f"muscle_volume_kg:{mv.muscle_group}", mv.volume_kg),
                )

    def record_endurance_session(self, *, activity_raw_json: dict, context: EnduranceWorkoutContext) -> None:
        """Counterpart to record_strength_session for Run/Ride activities. Without
        this, endurance activities got briefed over Telegram but never landed in
        `activities` — every "this week" / weekly-digest read silently missed them."""
        with self._db.connection() as conn:
            conn.execute(
                """
                INSERT INTO activities (strava_id, sport_type, title, start_date, duration_s, raw_json)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (strava_id) DO UPDATE SET
                    title = EXCLUDED.title, raw_json = EXCLUDED.raw_json
                """,
                (
                    context.strava_activity_id, context.sport_type, activity_raw_json.get("name") or context.sport_type,
                    context.start_time, int(context.elapsed_time_s), psycopg_json(activity_raw_json),
                ),
            )

            conn.execute(
                "INSERT INTO metrics (source_id, source_type, metric_key, metric_value) VALUES (%s, 'activity', 'distance_m', %s)",
                (context.strava_activity_id, context.distance_m),
            )


def psycopg_json(data: dict):
    from psycopg.types.json import Json

    return Json(data)
