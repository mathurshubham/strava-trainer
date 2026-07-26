"""Explicit conversation state — the pronoun-resolution mechanism. PRD §7.

Never rely on the LLM alone to resolve "it" / "that set" / "the last one".
This state is injected into every prompt as a system block.
"""
from __future__ import annotations

import time
import uuid

from pydantic import BaseModel, Field

from app.models.enums import FocusType, Intent


class FocusRef(BaseModel):
    type: FocusType
    ref: str
    label: str
    set_at: float
    set_by: str  # "briefing" | "user_mention" | "explicit_command"


class EntityRef(BaseModel):
    type: str
    ref: str
    label: str
    ts: float


class Turn(BaseModel):
    role: str  # "user" | "assistant"
    text: str
    ts: float
    intent: Intent | None = None


class LastChart(BaseModel):
    chart_id: str
    params: dict = {}


class SessionState(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    chat_id: int
    created_at: float = Field(default_factory=time.time)
    last_active_at: float = Field(default_factory=time.time)
    expires_at: float

    focus: FocusRef | None = None
    entity_stack: list[EntityRef] = []  # most recent first, max 5
    turns: list[Turn] = []  # rolling, max 12

    data_cache_keys: list[str] = []
    last_chart: LastChart | None = None
    mode: str = "briefing"  # "briefing" | "conversation"

    def push_entity(self, entity: EntityRef, max_len: int = 5) -> None:
        self.entity_stack.insert(0, entity)
        del self.entity_stack[max_len:]

    def push_turn(self, turn: Turn, max_len: int = 12) -> None:
        self.turns.append(turn)
        if len(self.turns) > max_len:
            self.turns = self.turns[-max_len:]

    def set_focus(self, focus: FocusRef) -> None:
        self.focus = focus

    def touch(self, ttl_seconds: int) -> None:
        now = time.time()
        self.last_active_at = now
        self.expires_at = now + ttl_seconds

    def is_expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) > self.expires_at

    def prompt_context_block(self) -> str:
        """The exact block injected into every LLM prompt. PRD §7.2."""
        lines = []
        if self.focus:
            lines.append(f'CURRENT FOCUS: {self.focus.label} ({self.focus.type.value} {self.focus.ref})')
            lines.append(
                'Pronouns like "it", "this", "that session" refer to this unless '
                "the user clearly names something else."
            )
        if self.entity_stack:
            recent = ", ".join(e.label for e in self.entity_stack)
            lines.append(f"RECENTLY MENTIONED: {recent}")
        return "\n".join(lines)
