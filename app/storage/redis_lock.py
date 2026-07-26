"""Best-effort distributed lock over Redis SET NX EX. PRD §5.1: 'Wrap [token
refresh] in a Redis lock so concurrent workers don't race and invalidate
each other's tokens.' Single-tenant, low concurrency (PRD §1.2) — a spin-wait
with a bounded number of attempts is plenty; this is not trying to be a
general-purpose distributed lock.
"""
from __future__ import annotations

import time

from app.storage.redis import UpstashRedis


class RedisLock:
    def __init__(self, redis: UpstashRedis, key: str, ttl_seconds: int = 10, max_attempts: int = 50, retry_delay_s: float = 0.1) -> None:
        self._redis = redis
        self._key = key
        self._ttl = ttl_seconds
        self._max_attempts = max_attempts
        self._retry_delay_s = retry_delay_s

    def __enter__(self) -> "RedisLock":
        for _ in range(self._max_attempts):
            if self._redis.set_nx(self._key, "1", self._ttl):
                return self
            time.sleep(self._retry_delay_s)
        return self  # proceed rather than deadlock — worst case is one redundant token refresh

    def __exit__(self, *exc_info) -> None:
        self._redis.delete(self._key)
