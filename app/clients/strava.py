"""Strava API client: OAuth refresh (with a Redis lock to prevent concurrent
workers racing and invalidating each other's tokens), rate-limit tracking,
and the activity/stream/lap endpoints. PRD §2.3, §2.4, §5.1.

Webhook payloads are NEVER used as data here (PRD §5.2) — every call re-fetches
from the API using the object_id the webhook gave us.
"""
from __future__ import annotations

import time
from contextlib import AbstractContextManager
from typing import Callable, Protocol

import httpx
from pydantic import BaseModel

STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
REFRESH_SAFETY_MARGIN_S = 300
RATE_LIMIT_WARN_THRESHOLD = 0.8


class StravaTokens(BaseModel):
    access_token: str
    refresh_token: str
    expires_at: int  # unix timestamp


class TokenStore(Protocol):
    def get(self) -> StravaTokens: ...
    def save(self, tokens: StravaTokens) -> None: ...


class StravaRateLimitExceeded(RuntimeError):
    pass


class RateLimitState(BaseModel):
    limit_15min: int | None = None
    usage_15min: int | None = None
    limit_daily: int | None = None
    usage_daily: int | None = None

    def pct_15min(self) -> float | None:
        if not self.limit_15min:
            return None
        return self.usage_15min / self.limit_15min

    def pct_daily(self) -> float | None:
        if not self.limit_daily:
            return None
        return self.usage_daily / self.limit_daily

    def over_threshold(self, threshold: float = RATE_LIMIT_WARN_THRESHOLD) -> bool:
        for pct in (self.pct_15min(), self.pct_daily()):
            if pct is not None and pct > threshold:
                return True
        return False


class StravaClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        api_base: str,
        token_store: TokenStore,
        lock_factory: Callable[[], AbstractContextManager],
        http_client: httpx.Client | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._api_base = api_base.rstrip("/")
        self._token_store = token_store
        self._lock_factory = lock_factory
        self._http = http_client or httpx.Client(timeout=15.0)
        self.rate_limit = RateLimitState()

    # ── auth ──────────────────────────────────────────────────────────

    def _ensure_fresh_token(self) -> str:
        tokens = self._token_store.get()
        if tokens.expires_at - time.time() >= REFRESH_SAFETY_MARGIN_S:
            return tokens.access_token

        with self._lock_factory():
            tokens = self._token_store.get()  # re-check: another worker may have refreshed already
            if tokens.expires_at - time.time() < REFRESH_SAFETY_MARGIN_S:
                tokens = self._refresh(tokens.refresh_token)
                self._token_store.save(tokens)
        return tokens.access_token

    def _refresh(self, refresh_token: str) -> StravaTokens:
        response = self._http.post(
            STRAVA_TOKEN_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        response.raise_for_status()
        data = response.json()
        # Strava may issue a new refresh_token — always persist whatever comes back.
        return StravaTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", refresh_token),
            expires_at=data["expires_at"],
        )

    # ── rate limiting ─────────────────────────────────────────────────

    def _update_rate_limit(self, headers: httpx.Headers) -> None:
        limit = headers.get("X-RateLimit-Limit")
        usage = headers.get("X-RateLimit-Usage")
        if not limit or not usage:
            return
        limit_15, limit_daily = (int(x) for x in limit.split(","))
        usage_15, usage_daily = (int(x) for x in usage.split(","))
        self.rate_limit = RateLimitState(
            limit_15min=limit_15, usage_15min=usage_15, limit_daily=limit_daily, usage_daily=usage_daily
        )

    # ── request plumbing ──────────────────────────────────────────────

    def _request(self, method: str, path: str, *, essential: bool = True, **kwargs) -> httpx.Response:
        if not essential and self.rate_limit.over_threshold():
            raise StravaRateLimitExceeded(
                f"Refusing non-essential Strava call — usage above {RATE_LIMIT_WARN_THRESHOLD:.0%}."
            )
        token = self._ensure_fresh_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        response = self._http.request(method, self._api_base + path, headers=headers, **kwargs)
        self._update_rate_limit(response.headers)
        response.raise_for_status()
        return response

    # ── endpoints (PRD §2.3) ───────────────────────────────────────────

    def get_athlete(self) -> dict:
        return self._request("GET", "/athlete").json()

    def get_activity(self, activity_id: int) -> dict:
        return self._request("GET", f"/activities/{activity_id}").json()

    def get_activity_streams(self, activity_id: int, keys: list[str], resolution: str = "high") -> dict:
        params = {"keys": ",".join(keys), "key_by_type": "true", "resolution": resolution}
        return self._request("GET", f"/activities/{activity_id}/streams", params=params).json()

    def get_activity_laps(self, activity_id: int) -> list[dict]:
        return self._request("GET", f"/activities/{activity_id}/laps").json()

    def get_activity_zones(self, activity_id: int) -> list[dict]:
        # Summit/subscription feature — non-essential, gated by rate-limit threshold.
        return self._request("GET", f"/activities/{activity_id}/zones", essential=False).json()

    def get_athlete_zones(self) -> dict:
        return self._request("GET", "/athlete/zones", essential=False).json()

    def get_athlete_activities(self, *, before: int | None = None, after: int | None = None, page: int = 1, per_page: int = 30) -> list[dict]:
        params = {"page": page, "per_page": per_page}
        if before:
            params["before"] = before
        if after:
            params["after"] = after
        return self._request("GET", "/athlete/activities", params=params, essential=False).json()
