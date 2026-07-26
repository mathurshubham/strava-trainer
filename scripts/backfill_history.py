#!/usr/bin/env python3
"""Manual, on-demand load of recent Strava activities into Postgres — the
same persistence the live webhook path does, triggered by hand instead of
waiting for Strava to push. Two uses: (1) seed history from before the app
was ever installed, so PR/comparison baselines exist before the first live
webhook fires (PRD Appendix C.2 spirit — build against real data, not a
cold-start guess); (2) reload everything after `scripts/delete_all_data.py`,
or to backfill activities the live path missed (e.g. runs before
record_endurance_session existed — see DECISIONS.md 2026-07-26).

Requires the OAuth flow to already be complete (scripts/bootstrap_oauth.py).

Usage: python scripts/backfill_history.py [--days 90] [--limit 100]

Pulls a single page of up to `--limit` activities (all sports combined,
most-recent-first) from the trailing `--days` window, then records both
weight-training and endurance (Run/Ride/etc.) ones. 100 is Strava's default
page size and plenty for a personal account's backfill — raise `--limit`
(Strava's API max is 200) only if you genuinely have more total activity than
that in the window.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.agents.data_agent import build_endurance_context, build_strength_context
from app.clients.strava import StravaClient
from app.config import get_settings
from app.metrics.muscle_mapping import normalize_exercise_name
from app.parsers.hevy_strava_description import parse_hevy_strava_description
from app.security.crypto import TokenCipher
from app.storage.db import Database
from app.storage.exercise_history import ExerciseHistoryRepo
from app.storage.oauth_tokens import PostgresTokenStore
from app.storage.profile import ProfileRepo
from app.storage.redis import UpstashRedis
from app.storage.redis_lock import RedisLock


def _load_strength(strava: StravaClient, history: ExerciseHistoryRepo, activity: dict) -> None:
    detail = strava.get_activity(activity["id"])
    start_time = datetime.fromisoformat(detail["start_date"].replace("Z", "+00:00"))

    previous_occurrences, exercise_bests = {}, {}
    parsed = parse_hevy_strava_description(detail.get("description") or "", detail.get("name", "Workout"))
    for ex in parsed.exercises:
        key = normalize_exercise_name(ex.exercise_name)
        prev = history.get_previous_occurrence(key, before=start_time)
        if prev:
            previous_occurrences[key] = prev
        exercise_bests[key] = history.get_exercise_bests(key)
    session_bests = history.get_session_bests()

    context = build_strength_context(
        detail, previous_occurrences=previous_occurrences, exercise_bests=exercise_bests, session_bests=session_bests,
    )
    history.record_strength_session(activity_raw_json=detail, context=context)
    print(f"  [lift] Recorded {detail.get('name')} ({start_time:%Y-%m-%d}) — {len(context.exercises)} exercises, {context.total_volume_kg:.0f}kg volume")
    if parsed.parser_warnings:
        for w in parsed.parser_warnings:
            print(f"    ⚠ {w}")


def _load_endurance(strava: StravaClient, history: ExerciseHistoryRepo, profile_repo: ProfileRepo, activity: dict) -> None:
    detail = strava.get_activity(activity["id"])
    start_time = datetime.fromisoformat(detail["start_date"].replace("Z", "+00:00"))
    streams = strava.get_activity_streams(
        detail["id"], ["time", "heartrate", "distance", "altitude", "velocity_smooth", "cadence", "grade_smooth", "moving"],
    )
    laps = strava.get_activity_laps(detail["id"])
    profile = profile_repo.get()

    context = build_endurance_context(
        detail, streams, laps, profile=profile, median_28d_distance_m=None, acute_7d_load=0, chronic_28d_load=0,
    )
    history.record_endurance_session(activity_raw_json=detail, context=context)
    print(f"  [{context.sport_type.lower()}] Recorded {detail.get('name')} ({start_time:%Y-%m-%d}) — {context.distance_m/1000:.2f}km in {context.moving_time_s/60:.0f}min")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--limit", type=int, default=100, help="Max activities to fetch (single page, Strava's API max is 200)")
    args = parser.parse_args()

    settings = get_settings()
    db = Database(settings.database_url)
    redis = UpstashRedis(settings.upstash_redis_rest_url, settings.upstash_redis_rest_token)
    token_store = PostgresTokenStore(db, TokenCipher(settings.encryption_key))
    strava = StravaClient(
        client_id=settings.strava_client_id, client_secret=settings.strava_client_secret,
        api_base=settings.strava_api_base, token_store=token_store,
        lock_factory=lambda: RedisLock(redis, "lock:strava_token_refresh"),
    )
    history = ExerciseHistoryRepo(db)
    profile_repo = ProfileRepo(db)

    after = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp())
    activities = strava.get_athlete_activities(after=after, per_page=args.limit)
    strength_activities = [a for a in activities if a.get("sport_type") == "WeightTraining"]
    endurance_activities = [a for a in activities if a.get("sport_type") != "WeightTraining"]
    strength_activities.sort(key=lambda a: a["start_date"])  # oldest first, so PRs land in the right order
    endurance_activities.sort(key=lambda a: a["start_date"])

    print(
        f"Found {len(activities)} total activities (limit {args.limit}) in the last {args.days} days: "
        f"{len(strength_activities)} weight-training, {len(endurance_activities)} endurance."
    )

    for activity in strength_activities:
        _load_strength(strava, history, activity)
        time.sleep(0.5)  # be gentle with Strava's rate limit (PRD §2.4)

    for activity in endurance_activities:
        _load_endurance(strava, history, profile_repo, activity)
        time.sleep(0.5)

    print("Done.")


if __name__ == "__main__":
    main()
