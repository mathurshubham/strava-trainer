#!/usr/bin/env python3
"""Debug helper: fetch this week's activities straight from the Strava API,
bypassing Postgres/orchestrator/LLM entirely, so you can compare raw Strava
truth against whatever the bot is reporting.

Usage: python scripts/pull_week_isolated.py [--days 7]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.clients.strava import StravaClient
from app.config import get_settings
from app.security.crypto import TokenCipher
from app.storage.db import Database
from app.storage.oauth_tokens import PostgresTokenStore
from app.storage.redis import UpstashRedis
from app.storage.redis_lock import RedisLock


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
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

    after_dt = datetime.now(timezone.utc) - timedelta(days=args.days)
    activities = strava.get_athlete_activities(after=int(after_dt.timestamp()), per_page=100)

    print(f"Querying Strava directly for activities after {after_dt:%Y-%m-%d %H:%M} UTC ({args.days} days)")
    print(f"Strava returned {len(activities)} activities:\n")

    for a in sorted(activities, key=lambda x: x["start_date"]):
        start = datetime.fromisoformat(a["start_date"].replace("Z", "+00:00"))
        print(f"- [{a['id']}] {start:%Y-%m-%d %H:%M} UTC  |  {a.get('name')!r}  |  sport_type={a.get('sport_type')}  "
              f"|  distance={a.get('distance', 0)/1000:.2f}km  |  moving_time={a.get('moving_time', 0)/60:.0f}min")

    if not activities:
        print("(none)")


if __name__ == "__main__":
    main()
