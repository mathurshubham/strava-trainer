"""Strava OAuth redirect handler. PRD §5.1, §8.2: CSRF `state` param, single-use,
10-minute TTL — `consume_state` is expected to be a check-and-delete against
Redis so a replayed callback URL can't re-trigger a token exchange.
"""
from __future__ import annotations

from typing import Callable, Protocol

import httpx

from app.clients.strava import StravaTokens
from app.web.types import WebResponse

STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"


class TokenSaver(Protocol):
    def save(self, tokens: StravaTokens) -> None: ...


def handle_get(
    query: dict,
    *,
    consume_state: Callable[[str], bool],
    client_id: str,
    client_secret: str,
    token_store: TokenSaver,
    http_client: httpx.Client | None = None,
) -> WebResponse:
    error = query.get("error")
    if error:
        return WebResponse(status=200, body=f"Authorization denied: {error}".encode(), content_type="text/plain")

    code, state = query.get("code"), query.get("state")
    if not code or not state:
        return WebResponse.json(400, {"error": "missing code or state"})
    if not consume_state(state):
        return WebResponse.json(400, {"error": "invalid or expired state — restart the authorization flow"})

    http = http_client or httpx.Client(timeout=15.0)
    response = http.post(STRAVA_TOKEN_URL, data={
        "client_id": client_id, "client_secret": client_secret, "code": code, "grant_type": "authorization_code",
    })
    response.raise_for_status()
    data = response.json()

    token_store.save(StravaTokens(access_token=data["access_token"], refresh_token=data["refresh_token"], expires_at=data["expires_at"]))
    return WebResponse(status=200, body=b"Strava connected. You can close this tab.", content_type="text/plain")
