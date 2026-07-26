"""PRD Phase 0 exit criteria: 'pytest green on config tests; gitleaks blocks a
planted fake secret.' This covers the fail-fast validation half."""
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.config import Settings


def _valid_kwargs(**overrides) -> dict:
    base = dict(
        telegram_bot_token="123456789:AAfaketokenfortestingonly1234567890",
        telegram_webhook_secret="a" * 32,
        telegram_owner_chat_id=111222333,
        strava_client_id="12345",
        strava_client_secret="a" * 40,
        strava_verify_token="b" * 32,
        strava_athlete_id=987654,
        gemini_api_key="fake-gemini-key",
        database_url="postgresql://user:pass@localhost/db",
        upstash_redis_rest_url="https://example.upstash.io",
        upstash_redis_rest_token="fake-redis-token",
        qstash_token="fake-qstash-token",
        qstash_current_signing_key="fake-current-key",
        qstash_next_signing_key="fake-next-key",
        app_url="https://app.example.com",
        encryption_key=Fernet.generate_key().decode(),
        internal_api_secret="c" * 32,
    )
    base.update(overrides)
    return base


def test_settings_load_with_all_required_vars():
    settings = Settings(_env_file=None, **_valid_kwargs())
    assert settings.strava_api_base == "https://www.strava.com/api/v3"
    assert settings.weight_unit == "kg"


def test_settings_fail_fast_on_missing_required_var():
    kwargs = _valid_kwargs()
    del kwargs["telegram_bot_token"]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **kwargs)


def test_settings_rejects_malformed_encryption_key():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **_valid_kwargs(encryption_key="not-a-fernet-key"))


def test_settings_rejects_short_secrets():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **_valid_kwargs(telegram_webhook_secret="short"))
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **_valid_kwargs(strava_verify_token="short"))
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **_valid_kwargs(internal_api_secret="short"))


def test_settings_treats_blank_env_string_as_none_for_optional_numerics():
    # Reproduces a real .env with unset optional numeric vars left as `KEY=`
    settings = Settings(_env_file=None, **_valid_kwargs(strava_subscription_id="", max_hr="", bodyweight_kg=""))
    assert settings.strava_subscription_id is None
    assert settings.max_hr is None
    assert settings.bodyweight_kg is None


def test_settings_defaults_are_sane():
    settings = Settings(_env_file=None, **_valid_kwargs())
    assert settings.session_ttl_seconds == 1800
    assert settings.strava_llm_analysis_enabled is True
    assert settings.strava_cache_ttl_days == 7
    assert settings.gemini_daily_call_cap == 200
