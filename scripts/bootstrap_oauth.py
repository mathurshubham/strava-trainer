#!/usr/bin/env python3
"""One-time Strava OAuth authorization. PRD §5.1.

Prints the authorize URL — open it yourself, log in, and approve. Strava
redirects to APP_URL/api/oauth/strava/callback, which exchanges the code for
tokens and stores them (encrypted) in Postgres. This script never sees your
Strava password; it only generates the URL and the CSRF state.

Usage: python scripts/bootstrap_oauth.py
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import get_settings
from app.storage.oauth_state import create_oauth_state
from app.storage.redis import UpstashRedis

SCOPES = "read,activity:read_all,profile:read_all"


def main() -> None:
    settings = get_settings()
    if not settings.app_url:
        print("APP_URL is not set in .env — set it to your deployed Vercel URL first.")
        raise SystemExit(1)

    redis = UpstashRedis(settings.upstash_redis_rest_url, settings.upstash_redis_rest_token)
    state = secrets.token_urlsafe(24)
    create_oauth_state(redis, state, ttl_seconds=600)

    params = {
        "client_id": settings.strava_client_id,
        "redirect_uri": f"{settings.app_url}/api/oauth/strava/callback",
        "response_type": "code",
        "approval_prompt": "force",
        "scope": SCOPES,
        "state": state,
    }
    url = "https://www.strava.com/oauth/authorize?" + urlencode(params)

    print("Open this URL, log in, and approve (valid for 10 minutes):\n")
    print(url)
    print("\nAfter approving, you'll land on a page saying 'Strava connected.'")


if __name__ == "__main__":
    main()
