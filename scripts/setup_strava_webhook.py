#!/usr/bin/env python3
"""Create/view/delete the Strava push subscription. PRD §5.2.

Usage:
    python scripts/setup_strava_webhook.py create
    python scripts/setup_strava_webhook.py view
    python scripts/setup_strava_webhook.py delete <subscription_id>

After `create` succeeds, copy the returned id into STRAVA_SUBSCRIPTION_ID in
.env (and in your Vercel project's env vars) and redeploy — the webhook POST
handler validates every event against that id.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import get_settings

STRAVA_PUSH_SUBSCRIPTIONS_URL = "https://www.strava.com/api/v3/push_subscriptions"


def create(settings) -> None:
    if not settings.app_url:
        print("APP_URL is not set in .env — set it to your deployed Vercel URL first.")
        raise SystemExit(1)
    response = httpx.post(
        STRAVA_PUSH_SUBSCRIPTIONS_URL,
        data={
            "client_id": settings.strava_client_id,
            "client_secret": settings.strava_client_secret,
            "callback_url": f"{settings.app_url}/api/webhook/strava",
            "verify_token": settings.strava_verify_token,
        },
    )
    print(response.status_code, response.text)
    if response.status_code == 200:
        print("\nSet STRAVA_SUBSCRIPTION_ID to the 'id' above in .env and in Vercel, then redeploy.")
    elif response.status_code in (401, 403):
        print("\nNote (PRD §5.2): webhook access has historically been gated for some apps.")
        print("If this persists, fall back to polling and email developers@strava.com.")


def view(settings) -> None:
    response = httpx.get(
        STRAVA_PUSH_SUBSCRIPTIONS_URL,
        params={"client_id": settings.strava_client_id, "client_secret": settings.strava_client_secret},
    )
    print(response.status_code, response.text)


def delete(settings, subscription_id: str) -> None:
    response = httpx.delete(
        f"{STRAVA_PUSH_SUBSCRIPTIONS_URL}/{subscription_id}",
        params={"client_id": settings.strava_client_id, "client_secret": settings.strava_client_secret},
    )
    print(response.status_code, response.text or "(deleted)")


def main() -> None:
    settings = get_settings()
    if len(sys.argv) < 2 or sys.argv[1] not in ("create", "view", "delete"):
        print(__doc__)
        raise SystemExit(1)

    command = sys.argv[1]
    if command == "create":
        create(settings)
    elif command == "view":
        view(settings)
    elif command == "delete":
        if len(sys.argv) < 3:
            print("Usage: python scripts/setup_strava_webhook.py delete <subscription_id>")
            raise SystemExit(1)
        delete(settings, sys.argv[2])


if __name__ == "__main__":
    main()
