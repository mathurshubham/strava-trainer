"""QStash publish helper — enqueues work for /api/worker so webhook handlers
can ack in <200ms and never do the actual (5-40s) LLM work inline (PRD §3.1).
"""
from __future__ import annotations

import httpx

QSTASH_PUBLISH_BASE = "https://qstash.upstash.io/v2/publish"


class QStashQueue:
    def __init__(self, token: str, http_client: httpx.Client | None = None) -> None:
        self._token = token
        self._http = http_client or httpx.Client(timeout=10.0)

    def publish(self, destination_url: str, payload: dict, delay_seconds: int | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._token}"}
        if delay_seconds:
            headers["Upstash-Delay"] = f"{delay_seconds}s"
        response = self._http.post(f"{QSTASH_PUBLISH_BASE}/{destination_url}", headers=headers, json=payload)
        response.raise_for_status()
        return response.json()
