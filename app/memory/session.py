"""Session lifecycle on top of Redis. PRD §7: 30-minute sliding-window sessions,
explicit focus state so pronoun resolution doesn't depend on the LLM alone.
"""
from __future__ import annotations

import re

from app.models.session import FocusRef, SessionState
from app.storage.redis import UpstashRedis

_PRONOUN_RE = re.compile(r"\b(it|that|this|those|them|the last one)\b", re.IGNORECASE)


def contains_pronoun(user_text: str) -> bool:
    return bool(_PRONOUN_RE.search(user_text))


def soft_confirmation_prefix(focus: FocusRef) -> str:
    """PRD §7.2: on session expiry, never ask 'what is it?' — soft-confirm instead."""
    return f"Picking up on your {focus.label} — "


class SessionManager:
    def __init__(self, redis: UpstashRedis, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    @staticmethod
    def _key(chat_id: int) -> str:
        return f"session:{chat_id}"

    def load(self, chat_id: int) -> SessionState | None:
        raw = self._redis.get(self._key(chat_id))
        if raw is None:
            return None
        state = SessionState.model_validate_json(raw)
        if state.is_expired():
            return None
        return state

    def save(self, state: SessionState) -> None:
        state.touch(self._ttl)
        self._redis.set(self._key(state.chat_id), state.model_dump_json(), ex=self._ttl)

    def forget(self, chat_id: int) -> None:
        """The /forget command — deletes immediately, no grace period (PRD §7.2)."""
        self._redis.delete(self._key(chat_id))

    def get_or_create(self, chat_id: int, fallback_focus: FocusRef | None = None) -> tuple[SessionState, bool]:
        """Returns (state, is_new_session). On a missing/expired session, starts a
        fresh one and — if a fallback focus (usually the most recent workout) is
        given — sets it immediately so pronouns keep resolving instead of failing."""
        existing = self.load(chat_id)
        if existing is not None:
            return existing, False

        state = SessionState(chat_id=chat_id, expires_at=0, mode="conversation")
        state.touch(self._ttl)
        if fallback_focus is not None:
            state.set_focus(fallback_focus)
        return state, True
