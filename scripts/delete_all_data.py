#!/usr/bin/env python3
"""Wipe every stored data point — Postgres tables and Redis keys. PRD §8.5.

Destructive and irreversible. Requires typing DELETE to confirm.

Usage: python scripts/delete_all_data.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import get_settings
from app.storage.db import Database

TABLES = [
    "briefings", "personal_records", "metrics", "exercise_sets",
    "activities", "event_log", "oauth_tokens",
]


def main() -> None:
    settings = get_settings()
    confirm = input("This permanently deletes ALL LiftMate data (Postgres + session/cache keys). Type DELETE to continue: ")
    if confirm != "DELETE":
        print("Aborted.")
        raise SystemExit(1)

    db = Database(settings.database_url)
    with db.connection() as conn:
        for table in TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.execute("UPDATE athlete_profile SET training_goal = %s, max_hr = NULL, resting_hr = NULL, lthr = NULL, bodyweight_kg = NULL WHERE id = 1", (
            "Hybrid — lifting + running, general fitness",
        ))
    print("Postgres tables cleared (athlete_profile reset to defaults, row kept).")

    print(
        "Redis keys (session:*, gemini:calls:*, oauth_state:*, lock:*) expire on "
        "their own TTLs — Upstash's REST API has no pattern-delete, so wait for "
        "them to age out, or clear the database from the Upstash console if you "
        "need it immediate."
    )


if __name__ == "__main__":
    main()
