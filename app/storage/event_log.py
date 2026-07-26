"""Idempotency guard for webhook events. QStash is at-least-once delivery —
the worker WILL see duplicates (PRD §3.6). `already_processed` uses the
UNIQUE constraint on event_hash as the actual race-safe guard, not a
check-then-insert (two concurrent workers could both pass a SELECT check).
"""
from __future__ import annotations

import json

from app.storage.db import Database


class EventLog:
    def __init__(self, db: Database) -> None:
        self._db = db

    def try_claim(self, *, provider: str, event_hash: str, payload: dict) -> bool:
        """Returns True if this event was newly claimed (process it), False if
        it's a duplicate (already seen — skip)."""
        with self._db.connection() as conn:
            row = conn.execute(
                """
                INSERT INTO event_log (provider, event_hash, payload, status)
                VALUES (%s, %s, %s, 'processing')
                ON CONFLICT (event_hash) DO NOTHING
                RETURNING id
                """,
                (provider, event_hash, json.dumps(payload)),
            ).fetchone()
        return row is not None

    def mark_done(self, event_hash: str) -> None:
        with self._db.connection() as conn:
            conn.execute(
                "UPDATE event_log SET status = 'done', processed_at = now() WHERE event_hash = %s",
                (event_hash,),
            )

    def mark_failed(self, event_hash: str) -> None:
        with self._db.connection() as conn:
            conn.execute(
                "UPDATE event_log SET status = 'failed', processed_at = now() WHERE event_hash = %s",
                (event_hash,),
            )
