"""CSRF `state` parameter for the Strava OAuth callback — single-use, 10-min
TTL in Redis (PRD §8.2)."""
from __future__ import annotations

from app.storage.redis import UpstashRedis

_PREFIX = "oauth_state:"


def create_oauth_state(redis: UpstashRedis, state: str, ttl_seconds: int = 600) -> None:
    redis.set(_PREFIX + state, "1", ex=ttl_seconds)


def consume_oauth_state(redis: UpstashRedis, state: str) -> bool:
    """Check-and-delete: True only the first time this state is seen."""
    if redis.get(_PREFIX + state) is None:
        return False
    redis.delete(_PREFIX + state)
    return True
