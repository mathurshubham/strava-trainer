"""The only place environment variables are read. Everything else imports `settings` from here.

Fails fast and loudly at import time if a required variable is missing or malformed —
see Appendix, PRD §8.1: "Never os.getenv(...) scattered through the codebase."
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Telegram ──────────────────────────────────────────
    telegram_bot_token: str
    telegram_webhook_secret: str
    telegram_owner_chat_id: int

    # ── Strava ────────────────────────────────────────────
    strava_client_id: str
    strava_client_secret: str
    strava_verify_token: str
    strava_athlete_id: int
    strava_api_base: str = "https://www.strava.com/api/v3"
    # Set after running scripts/setup_strava_webhook.py — Strava returns this
    # subscription id, which the webhook POST handler validates against
    # (PRD §8.2). Left optional so the app can deploy before that script runs.
    strava_subscription_id: int | None = None

    # ── Gemini ────────────────────────────────────────────
    gemini_api_key: str
    gemini_model_orchestrator: str = "gemini-3.1-flash-lite"
    gemini_model_analyst: str = "gemini-3.5-flash"
    gemini_model_conversation: str = "gemini-3.5-flash"
    gemini_daily_call_cap: int = 200

    # ── Storage ───────────────────────────────────────────
    database_url: str
    upstash_redis_rest_url: str
    upstash_redis_rest_token: str
    qstash_token: str
    qstash_current_signing_key: str
    qstash_next_signing_key: str

    # ── App ───────────────────────────────────────────────
    app_url: str
    encryption_key: str
    internal_api_secret: str
    session_ttl_seconds: int = 1800
    log_level: str = "INFO"
    strava_llm_analysis_enabled: bool = True
    strava_cache_ttl_days: int = 7
    briefing_min_duration_seconds: int = 0
    briefing_min_distance_meters: int = 0

    # ── Athlete profile (defaults; overridable at runtime via /profile) ──
    max_hr: int | None = None
    resting_hr: int | None = None
    lthr: int | None = None
    bodyweight_kg: float | None = None
    weight_unit: Literal["kg", "lbs"] = "kg"
    distance_unit: Literal["km", "mi"] = "km"
    training_goal: str = "Hybrid — lifting + running, general fitness"

    @field_validator("strava_subscription_id", "max_hr", "resting_hr", "lthr", "bodyweight_kg", mode="before")
    @classmethod
    def _blank_string_is_none(cls, v):
        # An unset numeric env var (e.g. STRAVA_SUBSCRIPTION_ID= before the
        # webhook setup script has run) arrives as "" from the .env file —
        # treat that as "not configured yet", not a parse error.
        return None if v == "" else v

    @field_validator("encryption_key")
    @classmethod
    def _fernet_key_shape(cls, v: str) -> str:
        # Fernet keys are 32 url-safe base64-encoded bytes -> 44 chars incl. padding.
        if len(v) != 44 or not v.endswith("="):
            raise ValueError(
                "ENCRYPTION_KEY must be a Fernet key. Generate one with: "
                "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        return v

    @field_validator("telegram_webhook_secret", "strava_verify_token", "internal_api_secret")
    @classmethod
    def _min_secret_length(cls, v: str) -> str:
        if len(v) < 16:
            raise ValueError("This secret is too short — use at least 16 random characters.")
        return v


@lru_cache
def get_settings() -> Settings:
    """Cached per-process. Call this at the top of each entrypoint module
    (api/*.py) so a serverless cold start fails fast and loudly on a missing
    or malformed var — not lazily, mid-request. Importing this module alone
    must never touch the environment, or every test that imports anything
    from `app` would need a fully populated .env."""
    return Settings()  # type: ignore[call-arg]
