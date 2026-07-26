import json as json_module
import time

import httpx
import pytest

from app.memory.session import SessionManager, contains_pronoun, soft_confirmation_prefix
from app.models.enums import FocusType
from app.models.session import EntityRef, FocusRef, SessionState, Turn
from app.storage.redis import UpstashRedis


def _mock_redis(store: dict) -> UpstashRedis:
    def handler(request: httpx.Request) -> httpx.Response:
        cmd = json_module.loads(request.content)
        op = cmd[0]
        if op == "GET":
            return httpx.Response(200, json={"result": store.get(cmd[1])})
        if op == "SET":
            store[cmd[1]] = cmd[2]
            return httpx.Response(200, json={"result": "OK"})
        if op == "DEL":
            existed = cmd[1] in store
            store.pop(cmd[1], None)
            return httpx.Response(200, json={"result": 1 if existed else 0})
        if op == "EXPIRE":
            return httpx.Response(200, json={"result": 1})
        return httpx.Response(400, json={"error": f"unhandled op {op}"})

    return UpstashRedis(url="https://fake.upstash.io", token="fake-token", client=httpx.Client(transport=httpx.MockTransport(handler)))


# ── SessionState pure logic ──────────────────────────────────────────────


def test_push_entity_keeps_most_recent_first_and_caps_length():
    state = SessionState(chat_id=1, expires_at=time.time() + 1800)
    for i in range(7):
        state.push_entity(EntityRef(type="exercise", ref=f"ex{i}", label=f"Exercise {i}", ts=time.time()))
    assert len(state.entity_stack) == 5
    assert state.entity_stack[0].ref == "ex6"  # most recent first


def test_push_turn_caps_at_12():
    state = SessionState(chat_id=1, expires_at=time.time() + 1800)
    for i in range(15):
        state.push_turn(Turn(role="user", text=f"msg {i}", ts=time.time()))
    assert len(state.turns) == 12
    assert state.turns[0].text == "msg 3"  # oldest trimmed


def test_prompt_context_block_includes_focus_and_recent_entities():
    state = SessionState(chat_id=1, expires_at=time.time() + 1800)
    state.set_focus(FocusRef(type=FocusType.WORKOUT, ref="hevy_abc123", label="Push Day A — 24 Jul", set_at=time.time(), set_by="briefing"))
    state.push_entity(EntityRef(type="exercise", ref="bench", label="Bench Press (Barbell)", ts=time.time()))
    block = state.prompt_context_block()
    assert "Push Day A — 24 Jul" in block
    assert "Bench Press (Barbell)" in block
    assert 'Pronouns like "it"' in block


def test_is_expired():
    state = SessionState(chat_id=1, expires_at=time.time() - 1)
    assert state.is_expired()
    state2 = SessionState(chat_id=1, expires_at=time.time() + 1800)
    assert not state2.is_expired()


def test_contains_pronoun():
    assert contains_pronoun("how was it?")
    assert contains_pronoun("compare it to Tuesday")
    assert not contains_pronoun("how was my push day session")


def test_soft_confirmation_prefix():
    focus = FocusRef(type=FocusType.WORKOUT, ref="x", label="Push Day A from Thursday", set_at=time.time(), set_by="briefing")
    assert soft_confirmation_prefix(focus) == "Picking up on your Push Day A from Thursday — "


# ── SessionManager (mocked Redis) ────────────────────────────────────────


def test_save_and_load_roundtrip():
    store: dict = {}
    manager = SessionManager(_mock_redis(store), ttl_seconds=1800)
    state = SessionState(chat_id=42, expires_at=0)
    state.set_focus(FocusRef(type=FocusType.WORKOUT, ref="w1", label="Push Day A", set_at=time.time(), set_by="briefing"))
    manager.save(state)

    loaded = manager.load(42)
    assert loaded is not None
    assert loaded.focus.label == "Push Day A"


def test_load_returns_none_when_expired():
    store: dict = {}
    manager = SessionManager(_mock_redis(store), ttl_seconds=1800)
    expired_state = SessionState(chat_id=42, expires_at=time.time() - 5)
    store["session:42"] = expired_state.model_dump_json()  # bypass save() so expiry isn't reset

    assert manager.load(42) is None


def test_forget_deletes_immediately():
    store: dict = {}
    manager = SessionManager(_mock_redis(store), ttl_seconds=1800)
    state = SessionState(chat_id=42, expires_at=0)
    manager.save(state)
    assert manager.load(42) is not None

    manager.forget(42)
    assert manager.load(42) is None


def test_get_or_create_falls_back_to_most_recent_workout_on_missing_session():
    store: dict = {}
    manager = SessionManager(_mock_redis(store), ttl_seconds=1800)
    fallback = FocusRef(type=FocusType.WORKOUT, ref="w99", label="Push Day A from Thursday", set_at=time.time(), set_by="briefing")

    state, is_new = manager.get_or_create(42, fallback_focus=fallback)
    assert is_new is True
    assert state.focus.label == "Push Day A from Thursday"


def test_get_or_create_returns_existing_session_when_present():
    store: dict = {}
    manager = SessionManager(_mock_redis(store), ttl_seconds=1800)
    original = SessionState(chat_id=42, expires_at=0)
    original.mode = "conversation"
    manager.save(original)

    state, is_new = manager.get_or_create(42, fallback_focus=None)
    assert is_new is False
    assert state.mode == "conversation"
