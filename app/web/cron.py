"""Scheduled jobs. PRD §8.5 (cache purge) and §5.3 commands (/week ~ weekly digest).
Thin wrappers around app/storage — kept here (not in api/cron/*.py) so the
actual logic is importable and unit-testable without a live Postgres."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.storage.db import Database


def purge_expired_activities(db: Database) -> int:
    """PRD §8.5: 'Purge activities.raw_json per STRAVA_CACHE_TTL_DAYS.' Clears
    the raw payload but keeps the row (derived metrics/PRs already live in
    their own tables and must survive — only the raw cache is time-limited)."""
    with db.connection() as conn:
        cursor = conn.execute(
            "UPDATE activities SET raw_json = '{}'::jsonb WHERE expires_at IS NOT NULL AND expires_at < now() AND raw_json != '{}'::jsonb"
        )
        return cursor.rowcount


def compute_weekly_stats(db: Database) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=7)
    with db.connection() as conn:
        # Join to activities.start_date rather than filtering on metrics.computed_at —
        # computed_at is when the row was INSERTED, not when the workout happened, so a
        # backfill/reload run (scripts/backfill_history.py) would otherwise make every
        # historical session's volume count as "this week" the day it's (re)loaded.
        volume_row = conn.execute(
            """
            SELECT COALESCE(SUM(m.metric_value), 0)
            FROM metrics m JOIN activities a ON a.strava_id = m.source_id
            WHERE m.metric_key = 'total_volume_kg' AND m.source_type = 'activity' AND a.start_date >= %s
            """,
            (since,),
        ).fetchone()
        distance_row = conn.execute(
            "SELECT COALESCE(SUM((raw_json->>'distance')::float), 0) FROM activities WHERE start_date >= %s AND sport_type != 'WeightTraining'",
            (since,),
        ).fetchone()
        count_row = conn.execute("SELECT COUNT(*) FROM activities WHERE start_date >= %s", (since,)).fetchone()

    return {
        "total_volume_kg": volume_row[0],
        "total_distance_km": (distance_row[0] or 0) / 1000,
        "session_count": count_row[0],
    }


def weekly_digest_text(*, total_volume_kg: float, total_distance_km: float, session_count: int) -> str:
    return (
        "**This week**\n"
        f"- Total lifting volume: {total_volume_kg:.0f}kg\n"
        f"- Total distance: {total_distance_km:.1f}km\n"
        f"- Sessions logged: {session_count}"
    )
