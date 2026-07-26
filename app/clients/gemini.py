"""Gemini client: structured output, retry-on-invalid, and a hard daily call
cap enforced via Redis (PRD §8.4 — 'the' circuit breaker for cost control).

Model IDs are never hardcoded past config (PRD §2.4: 'Put model IDs in env
vars — verify against the live docs on first run and fail loudly if a model
ID 404s'). Confirmed live on 2026-07-26 (see DECISIONS.md): gemini-3.5-flash
and gemini-3.1-flash-lite are both currently available; gemini-3.6-flash and
gemini-3.5-flash-lite also exist if you want to switch later — just change
the env var, nothing in code.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.storage.redis import UpstashRedis

T = TypeVar("T", bound=BaseModel)


class GeminiDailyCapExceeded(RuntimeError):
    pass


class GeminiStructuredOutputError(RuntimeError):
    pass


def _seconds_until_midnight_utc() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((tomorrow - now).total_seconds())


class DailyCallCounter:
    """PRD §8.4: 'Enforced with a Redis counter, reset at midnight UTC.'"""

    def __init__(self, redis: UpstashRedis, cap: int) -> None:
        self._redis = redis
        self._cap = cap

    def check_and_increment(self) -> int:
        key = f"gemini:calls:{datetime.now(timezone.utc):%Y-%m-%d}"
        count = self._redis.incr(key)
        if count == 1:
            self._redis.expire(key, _seconds_until_midnight_utc())
        if count > self._cap:
            raise GeminiDailyCapExceeded(f"Daily Gemini call cap ({self._cap}) exceeded — deterministic metrics only until UTC midnight.")
        return count


class ModelRunner(Protocol):
    """Whatever exposes `.models.generate_content(model=, contents=, config=)` —
    satisfied by `google.genai.Client` in production and a fake in tests."""

    models: object


class GeminiClient:
    def __init__(self, runner: ModelRunner, call_counter: DailyCallCounter) -> None:
        self._runner = runner
        self._call_counter = call_counter

    def generate_structured(self, *, model: str, prompt: str, response_schema: type[T], max_retries: int = 1) -> T:
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            self._call_counter.check_and_increment()
            response = self._invoke(model, prompt, response_schema)
            try:
                return self._parse(response, response_schema)
            except (ValidationError, ValueError) as exc:
                last_error = exc
                continue
        raise GeminiStructuredOutputError(f"Gemini returned invalid structured output after {max_retries + 1} attempt(s).") from last_error

    def _invoke(self, model: str, prompt: str, response_schema: type[T]):
        from google.genai import types  # imported lazily so tests never need the real SDK installed as a hard dependency

        return self._runner.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=response_schema),
        )

    @staticmethod
    def _parse(response, response_schema: type[T]) -> T:
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, response_schema):
            return parsed
        text = getattr(response, "text", None)
        if not text:
            raise ValueError("Gemini response had neither `.parsed` nor `.text`.")
        return response_schema.model_validate_json(text)
