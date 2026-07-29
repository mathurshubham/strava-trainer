"""Worker dispatch tests: signature gate, routing by activity sport_type,
command handling, and the conversation path updating session memory. Uses
real agent classes wired to a fake Gemini runner (not fake agents) so the
prompt-formatting/structured-output plumbing is exercised for real; only the
network-touching leaves (Strava, Telegram, Postgres-backed repos) are faked.
"""
import json
import time
from datetime import datetime, timezone

import httpx
import jwt
import pytest

from app.agents.chart_agent import ChartAgent
from app.agents.conversation import ConversationAgent
from app.agents.endurance_analyst import EnduranceAnalystAgent
from app.agents.orchestrator import OrchestratorAgent
from app.agents.strength_analyst import StrengthAnalystAgent
from app.clients.gemini import DailyCallCounter, GeminiClient
from app.memory.session import SessionManager
from app.models.agent_io import (
    ChartSelection, ConversationReply, OrchestratorOutput, StrengthAnalysis, EnduranceAnalysis, TargetEntity,
)
from app.models.enums import Intent, RunClassification
from app.models.history import ExerciseBests, PreviousOccurrence, SessionBests
from app.models.parsed import RawParsedSet
from app.models.profile import AthleteProfile
from app.storage.redis import UpstashRedis
from app.web import worker as worker_module
from app.web.worker import WorkerDeps, _fetch_data_for_turn


ENDPOINT_URL = "https://app.example.com/api/worker"
CURRENT_KEY = "current-signing-key-0123456789"
NEXT_KEY = "next-signing-key-0123456789"


def _sign(body: bytes, key: str = CURRENT_KEY) -> str:
    import base64
    import hashlib

    now = time.time()
    # QStash sends base64 of the digest, not hex — matches a real captured request.
    body_hash = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode()
    payload = {"iss": "Upstash", "sub": ENDPOINT_URL, "exp": now + 60, "nbf": now - 5, "body": body_hash}
    return jwt.encode(payload, key, algorithm="HS256")


class FakeStrava:
    def __init__(self, activity: dict, streams: dict | None = None, laps: list | None = None) -> None:
        self._activity = activity
        self._streams = streams or {}
        self._laps = laps or []

    def get_activity(self, activity_id):
        return self._activity

    def get_activity_streams(self, activity_id, keys, resolution="high"):
        return self._streams

    def get_activity_laps(self, activity_id):
        return self._laps


class FakeTelegram:
    def __init__(self) -> None:
        self.sent_markdown: list[tuple] = []
        self.sent_photos: list[tuple] = []
        self.chat_actions: list[int] = []

    def send_markdown(self, chat_id, markdown, reply_markup=None):
        self.sent_markdown.append((chat_id, markdown, reply_markup))
        return [{"ok": True}]

    def send_message(self, chat_id, html_text, reply_markup=None):
        return self.send_markdown(chat_id, html_text, reply_markup)

    def send_photo(self, chat_id, photo, caption_html=None):
        self.sent_photos.append((chat_id, caption_html))
        return {"ok": True}

    def send_chat_action(self, chat_id, action="typing"):
        self.chat_actions.append(chat_id)


class FakeProfileRepo:
    def __init__(self, profile: AthleteProfile) -> None:
        self.profile = profile
        self.updates: list[dict] = []

    def get(self):
        return self.profile

    def update(self, **fields):
        self.updates.append(fields)
        for k, v in fields.items():
            setattr(self.profile, k, v)
        return self.profile


class FakeExerciseHistory:
    def __init__(self) -> None:
        self.recorded = []
        self.bests_by_key: dict = {}
        self.previous_by_key: dict = {}
        self.e1rm_series_by_key: dict = {}
        self.volume_series: list = []
        self.pr_timeline: list = []
        self.session_dates: list = []

    def get_previous_occurrence(self, exercise_key, before):
        return self.previous_by_key.get(exercise_key)

    def get_e1rm_series(self, exercise_key):
        return self.e1rm_series_by_key.get(exercise_key, [])

    def get_volume_series(self):
        return self.volume_series

    def get_pr_timeline(self):
        return self.pr_timeline

    def get_session_dates(self):
        return self.session_dates

    def get_exercise_bests(self, exercise_key):
        return self.bests_by_key.get(exercise_key, ExerciseBests())

    def get_session_bests(self):
        return SessionBests()

    def record_strength_session(self, *, activity_raw_json, context):
        self.recorded.append(context.strava_activity_id)

    def record_endurance_session(self, *, activity_raw_json, context):
        self.recorded.append(context.strava_activity_id)

    def get_most_recent_activity_id(self):
        return getattr(self, "most_recent_activity_id", None)


class FakeEventLog:
    def __init__(self) -> None:
        self.done: list[str] = []
        self.failed: list[str] = []

    def mark_done(self, event_hash):
        self.done.append(event_hash)

    def mark_failed(self, event_hash):
        self.failed.append(event_hash)


class FakeGeminiModels:
    def __init__(self, response_by_schema: dict) -> None:
        self._by_schema = response_by_schema
        self.calls: list[tuple] = []  # (schema, prompt_text) — lets tests assert on what the LLM actually saw

    def generate_content(self, *, model, contents, config):
        schema = config.response_schema
        self.calls.append((schema, contents))
        return type("R", (), {"parsed": self._by_schema[schema], "text": None})()


class FakeGeminiRunner:
    def __init__(self, response_by_schema: dict) -> None:
        self.models = FakeGeminiModels(response_by_schema)


class NullCounter:
    def check_and_increment(self):
        return 1


def _mock_redis(store: dict) -> UpstashRedis:
    def handler(request: httpx.Request) -> httpx.Response:
        cmd = json.loads(request.content)
        op = cmd[0]
        if op == "GET":
            return httpx.Response(200, json={"result": store.get(cmd[1])})
        if op == "SET":
            store[cmd[1]] = cmd[2]
            return httpx.Response(200, json={"result": "OK"})
        if op == "DEL":
            store.pop(cmd[1], None)
            return httpx.Response(200, json={"result": 1})
        if op == "EXPIRE":
            return httpx.Response(200, json={"result": 1})
        return httpx.Response(400, json={"error": "unhandled"})

    return UpstashRedis(url="https://fake.upstash.io", token="tok", client=httpx.Client(transport=httpx.MockTransport(handler)))


def _make_deps(strava, telegram, *, session_store=None, gemini_responses=None, exercise_history=None, db=None) -> WorkerDeps:
    responses = gemini_responses or {}
    runner = FakeGeminiRunner(responses)
    gemini = GeminiClient(runner, NullCounter())
    deps = WorkerDeps(
        owner_chat_id=111,
        training_goal_default="Hybrid",
        strava=strava,
        telegram=telegram,
        orchestrator=OrchestratorAgent(gemini, model="m"),
        strength_analyst=StrengthAnalystAgent(gemini, model="m"),
        endurance_analyst=EnduranceAnalystAgent(gemini, model="m"),
        chart_agent=ChartAgent(gemini, model="m"),
        conversation=ConversationAgent(gemini, model="m"),
        session_manager=SessionManager(_mock_redis(session_store if session_store is not None else {}), ttl_seconds=1800),
        profile_repo=FakeProfileRepo(AthleteProfile()),
        exercise_history=exercise_history or FakeExerciseHistory(),
        event_log=FakeEventLog(),
        db=db,
    )
    deps._gemini_calls = runner.models.calls  # test-only escape hatch, see FakeGeminiModels
    return deps


def _post(payload: dict, deps: WorkerDeps):
    body = json.dumps(payload).encode()
    return worker_module.handle_post(
        {"Upstash-Signature": _sign(body)}, body,
        endpoint_url=ENDPOINT_URL, current_signing_key=CURRENT_KEY, next_signing_key=NEXT_KEY, deps=deps,
    )


def test_invalid_signature_rejected():
    deps = _make_deps(FakeStrava({}), FakeTelegram())
    resp = worker_module.handle_post(
        {"Upstash-Signature": "garbage"}, b"{}",
        endpoint_url=ENDPOINT_URL, current_signing_key=CURRENT_KEY, next_signing_key=NEXT_KEY, deps=deps,
    )
    assert resp.status == 401


def test_strava_activity_delete_is_a_noop():
    strava = FakeStrava({"id": 1})
    telegram = FakeTelegram()
    deps = _make_deps(strava, telegram)

    resp = _post({"kind": "strava_activity", "object_id": 1, "aspect_type": "delete", "event_hash": "h1"}, deps)
    assert resp.status == 200
    assert telegram.sent_markdown == []
    assert deps.event_log.done == ["h1"]


def test_weight_training_activity_sends_strength_briefing():
    activity = {
        "id": 42, "sport_type": "WeightTraining", "name": "Push Day A",
        "start_date": "2026-07-26T07:00:00Z", "elapsed_time": 3600,
        "description": "Bench Press (Barbell)\n60kg x 8\n60kg x 8\n",
    }
    telegram = FakeTelegram()
    deps = _make_deps(FakeStrava(activity), telegram, gemini_responses={
        StrengthAnalysis: StrengthAnalysis(headline="Solid push day", what_went_well=["a"], what_to_improve=["b"], next_session="more"),
    })

    resp = _post({"kind": "strava_activity", "object_id": 42, "aspect_type": "create", "event_hash": "h2"}, deps)

    assert resp.status == 200
    assert len(telegram.sent_markdown) == 4  # headline, breakdown, analysis, hook (chart sent separately as photo)
    assert "Solid push day" in telegram.sent_markdown[0][1]
    assert telegram.sent_markdown[-1][2] == worker_module.BRIEFING_HOOK_KEYBOARD
    assert len(telegram.sent_photos) == 1
    assert deps.exercise_history.recorded == [42]


def test_run_activity_sends_endurance_briefing():
    n = 50
    activity = {
        "id": 43, "type": "Run", "start_date": "2026-07-26T06:00:00Z",
        "distance": 8000, "moving_time": 2400, "elapsed_time": 2450, "average_heartrate": 145,
    }
    streams = {
        "time": {"data": [i * 48 for i in range(n)]},
        "heartrate": {"data": [140 + (i % 10) for i in range(n)]},
        "distance": {"data": [i * 160 for i in range(n)]},
        "moving": {"data": [True] * n},
    }
    telegram = FakeTelegram()
    deps = _make_deps(FakeStrava(activity, streams=streams), telegram, gemini_responses={
        EnduranceAnalysis: EnduranceAnalysis(
            classification=RunClassification.EASY_RECOVERY, classification_confidence=0.8, headline="Nice easy run",
            execution="on target", what_went_well=["a"], what_to_improve=["b"], next_session="repeat", load_context="fine",
        ),
    })

    resp = _post({"kind": "strava_activity", "object_id": 43, "aspect_type": "create", "event_hash": "h3"}, deps)

    assert resp.status == 200
    assert "Nice easy run" in telegram.sent_markdown[0][1]
    assert deps.exercise_history.recorded == [43]  # was silently dropped before — this activity never landed in `activities`


def test_command_start():
    telegram = FakeTelegram()
    deps = _make_deps(FakeStrava({}), telegram)
    _post({"kind": "telegram_update", "update": {"message": {"chat": {"id": 111}, "text": "/start"}}}, deps)
    assert "LiftMate" in telegram.sent_markdown[0][1]


def test_command_profile_goal_update():
    telegram = FakeTelegram()
    deps = _make_deps(FakeStrava({}), telegram)
    _post({"kind": "telegram_update", "update": {"message": {"chat": {"id": 111}, "text": "/profile goal Strength"}}}, deps)
    assert deps.profile_repo.updates == [{"training_goal": "Strength"}]
    assert "Strength" in telegram.sent_markdown[0][1]


def test_command_forget_clears_session():
    telegram = FakeTelegram()
    store: dict = {}
    deps = _make_deps(FakeStrava({}), telegram, session_store=store)
    deps.session_manager.save(deps.session_manager.get_or_create(111)[0])
    assert deps.session_manager.load(111) is not None

    _post({"kind": "telegram_update", "update": {"message": {"chat": {"id": 111}, "text": "/forget"}}}, deps)
    assert deps.session_manager.load(111) is None


def test_conversation_turn_routes_through_orchestrator_and_saves_session():
    telegram = FakeTelegram()
    deps = _make_deps(FakeStrava({}), telegram, gemini_responses={
        OrchestratorOutput: OrchestratorOutput(
            intent=Intent.QUESTION_ABOUT_WORKOUT, target_entity=None, needs_data=["none"],
            needs_chart=False, resolved_pronouns=[], route_to="conversation",
        ),
        ConversationReply: ConversationReply(reply_markdown="**It went well.**", updated_focus=None),
    })

    _post({"kind": "telegram_update", "update": {"message": {"chat": {"id": 111}, "text": "how was it?"}}}, deps)

    assert telegram.sent_markdown[0][1] == "**It went well.**"
    state = deps.session_manager.load(111)
    assert state is not None
    assert state.turns[-1].text == "**It went well.**"


def test_command_last_resends_most_recent_briefing():
    activity = {
        "id": 42, "sport_type": "WeightTraining", "name": "Push Day A",
        "start_date": "2026-07-26T07:00:00Z", "elapsed_time": 3600,
        "description": "Bench Press (Barbell)\n60kg x 8\n",
    }
    telegram = FakeTelegram()
    deps = _make_deps(FakeStrava(activity), telegram, gemini_responses={
        StrengthAnalysis: StrengthAnalysis(headline="Solid", what_went_well=["a"], what_to_improve=["b"], next_session="more"),
    })
    deps.exercise_history.most_recent_activity_id = 42

    _post({"kind": "telegram_update", "update": {"message": {"chat": {"id": 111}, "text": "/last"}}}, deps)
    assert "Solid" in telegram.sent_markdown[0][1]


def test_command_last_with_no_history_sends_friendly_message():
    telegram = FakeTelegram()
    deps = _make_deps(FakeStrava({}), telegram)
    _post({"kind": "telegram_update", "update": {"message": {"chat": {"id": 111}, "text": "/last"}}}, deps)
    assert "Nothing logged yet" in telegram.sent_markdown[0][1]


def test_non_owner_message_gets_private_notice():
    telegram = FakeTelegram()
    deps = _make_deps(FakeStrava({}), telegram)
    _post({"kind": "telegram_non_owner", "chat_id": 999}, deps)
    assert telegram.sent_markdown[0] == (999, "This bot is private.", None)


def test_worker_error_marks_event_failed_but_still_returns_200():
    class ExplodingStrava(FakeStrava):
        def get_activity(self, activity_id):
            raise RuntimeError("Strava is down")

    deps = _make_deps(ExplodingStrava({}), FakeTelegram())
    resp = _post({"kind": "strava_activity", "object_id": 1, "aspect_type": "create", "event_hash": "h-fail"}, deps)
    assert resp.status == 200
    assert deps.event_log.failed == ["h-fail"]


# ── Conversational data-fetch (the "bot can't answer about my history" bug) ──
#
# Regression coverage for a real defect: _handle_conversation_turn used to call
# deps.conversation.reply(fetched_data_json="{}", raw_activity_text="", ...)
# unconditionally, so the orchestrator's needs_data/target_entity verdict was
# computed and then thrown away. Every question that needed real data ("what's
# my bench PR?", "how's this week looked?") got a well-formed but empty context,
# and the LLM correctly-but-uselessly replied "I don't have that loaded."


def test_fetch_returns_empty_when_orchestrator_says_none_needed():
    deps = _make_deps(FakeStrava({}), FakeTelegram())
    route = OrchestratorOutput(
        intent=Intent.GENERAL_FITNESS, target_entity=None, needs_data=["none"],
        needs_chart=False, resolved_pronouns=[], route_to="conversation",
    )
    assert _fetch_data_for_turn(route, deps) == {}


def test_fetch_pulls_real_exercise_bests_and_previous_occurrence():
    history = FakeExerciseHistory()
    history.bests_by_key["bench_press"] = ExerciseBests(heaviest_weight_kg=100.0, best_e1rm_kg=110.0)
    history.previous_by_key["bench_press"] = PreviousOccurrence(
        date=datetime(2026, 7, 20, tzinfo=timezone.utc),
        sets=[RawParsedSet(weight_kg=95.0, reps=5)],
    )
    deps = _make_deps(FakeStrava({}), FakeTelegram(), exercise_history=history)

    route = OrchestratorOutput(
        intent=Intent.QUESTION_ABOUT_WORKOUT, target_entity=TargetEntity(type="exercise", ref="Bench Press (Barbell)"),
        needs_data=["exercise_history"], needs_chart=False, resolved_pronouns=[], route_to="conversation",
    )
    data = _fetch_data_for_turn(route, deps)

    assert data["exercise"] == "Bench Press (Barbell)"
    assert data["found_in_history"] is True
    assert data["bests"]["heaviest_weight_kg"] == 100.0
    assert data["most_recent_occurrence"]["sets"][0]["weight_kg"] == 95.0


def test_fetch_reports_not_found_for_a_never_logged_exercise():
    deps = _make_deps(FakeStrava({}), FakeTelegram())  # empty FakeExerciseHistory
    route = OrchestratorOutput(
        intent=Intent.QUESTION_ABOUT_WORKOUT, target_entity=TargetEntity(type="exercise", ref="Skull Crushers"),
        needs_data=["exercise_history"], needs_chart=False, resolved_pronouns=[], route_to="conversation",
    )
    data = _fetch_data_for_turn(route, deps)
    assert data == {
        "exercise": "Skull Crushers", "found_in_history": False,
        "bests": ExerciseBests().model_dump(), "most_recent_occurrence": None,
    }


def test_fetch_pulls_weekly_stats_for_a_period_question(monkeypatch):
    deps = _make_deps(FakeStrava({}), FakeTelegram(), db=object())  # any non-None sentinel; compute_weekly_stats is faked below
    captured_db = []

    def fake_weekly_stats(db):
        captured_db.append(db)
        return {"total_volume_kg": 4840.0, "total_distance_km": 12.3, "session_count": 3}

    monkeypatch.setattr(worker_module, "compute_weekly_stats", fake_weekly_stats)

    route = OrchestratorOutput(
        intent=Intent.HISTORY_QUERY, target_entity=TargetEntity(type="period", ref="this week"),
        needs_data=["exercise_history"], needs_chart=False, resolved_pronouns=[], route_to="conversation",
    )
    data = _fetch_data_for_turn(route, deps)

    assert captured_db == [deps.db]
    assert data == {"period": "this week", "total_volume_kg": 4840.0, "total_distance_km": 12.3, "session_count": 3}


def test_fetch_period_without_db_wired_returns_empty_rather_than_crashing():
    deps = _make_deps(FakeStrava({}), FakeTelegram())  # db defaults to None
    route = OrchestratorOutput(
        intent=Intent.HISTORY_QUERY, target_entity=TargetEntity(type="period", ref="this week"),
        needs_data=["exercise_history"], needs_chart=False, resolved_pronouns=[], route_to="conversation",
    )
    assert _fetch_data_for_turn(route, deps) == {}


def test_conversation_turn_actually_sends_fetched_data_to_the_llm():
    """End-to-end through handle_post: an exercise question must reach the LLM
    prompt with real numbers, not an empty '{}'."""
    history = FakeExerciseHistory()
    history.bests_by_key["bench_press"] = ExerciseBests(heaviest_weight_kg=100.0)
    telegram = FakeTelegram()
    deps = _make_deps(
        FakeStrava({}), telegram, exercise_history=history,
        gemini_responses={
            OrchestratorOutput: OrchestratorOutput(
                intent=Intent.QUESTION_ABOUT_WORKOUT, target_entity=TargetEntity(type="exercise", ref="bench press"),
                needs_data=["exercise_history"], needs_chart=False, resolved_pronouns=[], route_to="conversation",
            ),
            ConversationReply: ConversationReply(reply_markdown="Your bench PR is 100kg.", updated_focus=None),
        },
    )

    _post({"kind": "telegram_update", "update": {"message": {"chat": {"id": 111}, "text": "what's my bench PR?"}}}, deps)

    assert telegram.sent_markdown[0][1] == "Your bench PR is 100kg."
    conversation_prompt = next(text for schema, text in deps._gemini_calls if schema is ConversationReply)
    assert "100.0" in conversation_prompt or "100" in conversation_prompt
    assert conversation_prompt.count('"{}"') == 0  # the old bug: fetched_data_json hardcoded to an empty object


def _chart_deps(telegram, history, selection):
    """A conversation turn that wants a chart for the bench press: orchestrator
    sets needs_chart, chart agent returns `selection`."""
    return _make_deps(
        FakeStrava({}), telegram, exercise_history=history,
        gemini_responses={
            OrchestratorOutput: OrchestratorOutput(
                intent=Intent.QUESTION_ABOUT_WORKOUT, target_entity=TargetEntity(type="exercise", ref="bench press"),
                needs_data=["exercise_history"], needs_chart=True, resolved_pronouns=[], route_to="chart",
            ),
            ChartSelection: selection,
            # Present so the text-fallback branch can also complete without a KeyError.
            ConversationReply: ConversationReply(reply_markdown="I can't chart that one yet.", updated_focus=None),
        },
    )


def test_chart_turn_sends_a_photo_when_data_exists():
    history = FakeExerciseHistory()
    history.e1rm_series_by_key["bench_press"] = [
        (datetime(2026, 7, 1, tzinfo=timezone.utc), 100.0),
        (datetime(2026, 7, 8, tzinfo=timezone.utc), 102.5),
        (datetime(2026, 7, 15, tzinfo=timezone.utc), 105.0),
    ]
    telegram = FakeTelegram()
    deps = _chart_deps(telegram, history, ChartSelection(chart_id="s03_e1rm_trend", params=[], caption="Bench e1RM climbing"))

    _post({"kind": "telegram_update", "update": {"message": {"chat": {"id": 111}, "text": "chart my bench progress"}}}, deps)

    assert len(telegram.sent_photos) == 1
    chat_id, caption_html = telegram.sent_photos[0]
    assert chat_id == 111
    assert "Bench e1RM climbing" in caption_html
    assert telegram.sent_markdown == []  # no text reply when a chart was sent


def test_chart_turn_falls_back_to_text_when_history_too_thin():
    history = FakeExerciseHistory()
    history.e1rm_series_by_key["bench_press"] = [(datetime(2026, 7, 1, tzinfo=timezone.utc), 100.0)]  # only 1 point
    telegram = FakeTelegram()
    deps = _chart_deps(telegram, history, ChartSelection(chart_id="s03_e1rm_trend", params=[], caption="Bench e1RM"))

    _post({"kind": "telegram_update", "update": {"message": {"chat": {"id": 111}, "text": "chart my bench progress"}}}, deps)

    assert telegram.sent_photos == []
    assert telegram.sent_markdown[0][1] == "I can't chart that one yet."


def test_chart_turn_falls_back_to_text_for_unwired_chart_id():
    history = FakeExerciseHistory()
    telegram = FakeTelegram()
    deps = _chart_deps(telegram, history, ChartSelection(chart_id="e06_run_overlay", params=[], caption="Run overlay"))

    _post({"kind": "telegram_update", "update": {"message": {"chat": {"id": 111}, "text": "chart my run"}}}, deps)

    assert telegram.sent_photos == []
    assert telegram.sent_markdown[0][1] == "I can't chart that one yet."


def test_non_chart_turn_never_sends_a_photo():
    history = FakeExerciseHistory()
    history.bests_by_key["bench_press"] = ExerciseBests(heaviest_weight_kg=100.0)
    telegram = FakeTelegram()
    deps = _make_deps(
        FakeStrava({}), telegram, exercise_history=history,
        gemini_responses={
            OrchestratorOutput: OrchestratorOutput(
                intent=Intent.QUESTION_ABOUT_WORKOUT, target_entity=TargetEntity(type="exercise", ref="bench press"),
                needs_data=["exercise_history"], needs_chart=False, resolved_pronouns=[], route_to="conversation",
            ),
            ConversationReply: ConversationReply(reply_markdown="Your bench PR is 100kg.", updated_focus=None),
        },
    )

    _post({"kind": "telegram_update", "update": {"message": {"chat": {"id": 111}, "text": "what's my bench PR?"}}}, deps)

    assert telegram.sent_photos == []
    assert telegram.sent_markdown[0][1] == "Your bench PR is 100kg."
