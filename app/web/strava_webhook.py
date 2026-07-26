"""Strava webhook handler logic. PRD §3.1, §5.2, §8.2 — the single most
timing-sensitive endpoint in the system:

    GET  (validation): echo hub.challenge, <2s, after checking hub.verify_token
    POST (event):      ALWAYS 200, even on internal error — a non-200 costs
                        the event permanently (3 retries, then dropped forever)

The body is NEVER treated as data, only as a trigger — every value from it
gets re-fetched from the API using object_id before being used for anything.
That re-fetch happens in the worker, not here; this file only validates,
dedupes, and enqueues.
"""
from __future__ import annotations

import json
import logging

from app.security.auth import event_hash, verify_strava_verify_token, verify_strava_webhook_identity
from app.storage.event_log import EventLog
from app.storage.queue import QStashQueue
from app.web.types import WebResponse

logger = logging.getLogger(__name__)


def handle_get(query: dict, *, verify_token: str) -> WebResponse:
    if not verify_strava_verify_token(query.get("hub.verify_token"), verify_token):
        return WebResponse.json(403, {"error": "invalid verify_token"})
    return WebResponse.json(200, {"hub.challenge": query.get("hub.challenge", "")})


def handle_post(
    body: bytes,
    *,
    expected_subscription_id: int,
    expected_athlete_id: int,
    event_log: EventLog,
    queue: QStashQueue,
    worker_url: str,
) -> WebResponse:
    try:
        payload = json.loads(body)

        subscription_id = payload.get("subscription_id")
        owner_id = payload.get("owner_id")
        if not verify_strava_webhook_identity(
            subscription_id, owner_id,
            expected_subscription_id=expected_subscription_id, expected_athlete_id=expected_athlete_id,
        ):
            # Distinguish "not ours" (normal, quiet) from "we were never configured"
            # (a misconfiguration that drops EVERY event while still returning 200,
            # so neither Strava nor the logs would otherwise show anything wrong).
            if expected_subscription_id < 0:
                logger.error(
                    "STRAVA_SUBSCRIPTION_ID is not set — dropping webhook event for subscription %s. "
                    "Run `python scripts/setup_strava_webhook.py view` and put the id in .env.",
                    subscription_id,
                )
            else:
                logger.info(
                    "Ignoring webhook event: subscription %s / owner %s is not ours (expected %s / %s).",
                    subscription_id, owner_id, expected_subscription_id, expected_athlete_id,
                )
            return WebResponse.json(200, {"status": "ignored_not_ours"})

        object_type = payload.get("object_type")
        aspect_type = payload.get("aspect_type")
        object_id = payload.get("object_id")
        event_time = payload.get("event_time", 0)

        if object_type == "athlete" and payload.get("updates", {}).get("authorized") == "false":
            queue.publish(worker_url, {"kind": "strava_deauthorized"})
            return WebResponse.json(200, {"status": "deauth_queued"})

        h = event_hash("strava", str(object_id), aspect_type, event_time)
        if not event_log.try_claim(provider="strava", event_hash=h, payload=payload):
            return WebResponse.json(200, {"status": "duplicate"})

        queue.publish(worker_url, {
            "kind": "strava_activity",
            "object_id": object_id,
            "object_type": object_type,
            "aspect_type": aspect_type,
            "event_hash": h,
        })
        return WebResponse.json(200, {"status": "queued"})

    except Exception:
        # PRD §5.2: a non-200 here permanently drops the event after 3 retries.
        # An internal error must never cost us the event — log it, ack anyway.
        logger.exception("Unhandled error processing Strava webhook event")
        return WebResponse.json(200, {"status": "error_logged"})
