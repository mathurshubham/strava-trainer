"""Minimal Upstash Redis REST client. Sync httpx — matches the one-request-per-
invocation shape of a serverless function; no persistent connection to manage.

Uses the single-command REST form (`POST /` with a JSON array body) rather
than the path-style form (`/set/key/value`), since session-state JSON values
can be long and path-encoding them is fragile. https://upstash.com/docs/redis
"""
from __future__ import annotations

import httpx


class UpstashRedisError(RuntimeError):
    pass


class UpstashRedis:
    def __init__(self, url: str, token: str, timeout: float = 5.0, client: httpx.Client | None = None) -> None:
        self._base = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client or httpx.Client(timeout=timeout)

    def _command(self, *parts: str):
        response = self._client.post(self._base + "/", headers=self._headers, json=list(parts))
        response.raise_for_status()
        data = response.json()
        if data.get("error"):
            raise UpstashRedisError(data["error"])
        return data.get("result")

    def get(self, key: str) -> str | None:
        return self._command("GET", key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        if ex:
            self._command("SET", key, value, "EX", str(ex))
        else:
            self._command("SET", key, value)

    def set_nx(self, key: str, value: str, ex: int) -> bool:
        """SET key value NX EX ex — True if the lock was acquired."""
        return self._command("SET", key, value, "NX", "EX", str(ex)) == "OK"

    def delete(self, key: str) -> None:
        self._command("DEL", key)

    def expire(self, key: str, seconds: int) -> None:
        self._command("EXPIRE", key, str(seconds))

    def incr(self, key: str) -> int:
        return int(self._command("INCR", key))
