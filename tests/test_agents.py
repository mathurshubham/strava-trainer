"""Agent-layer tests: verify each agent's prompt loads/formats without error
(the real risk with file-based prompts + str.format) and that structured
output flows through correctly, using a fake Gemini runner — no live API key
needed. Also verifies the orchestrator's graceful fallback to `conversation`
when Gemini can't produce valid structured output (PRD §3.3)."""
import json

import pytest
from pydantic import BaseModel

from app.agents.chart_agent import ChartAgent
from app.agents.conversation import ConversationAgent
from app.agents.endurance_analyst import EnduranceAnalystAgent
from app.agents.orchestrator import OrchestratorAgent
from app.agents.strength_analyst import StrengthAnalystAgent
from app.clients.gemini import DailyCallCounter, GeminiClient
from app.models.agent_io import ChartSelection, ConversationReply, OrchestratorOutput, PronounResolution, StrengthAnalysis, TargetEntity
from app.models.enums import Intent, RunClassification
from app.models.workout import EnduranceWorkoutContext, StrengthWorkoutContext
from datetime import datetime


class FakeModels:
    def __init__(self, response) -> None:
        self._response = response
        self.last_contents = None

    def generate_content(self, *, model, contents, config):
        self.last_contents = contents
        return self._response


class FakeRunner:
    def __init__(self, response) -> None:
        self.models = FakeModels(response)


class FakeResponse:
    def __init__(self, parsed):
        self.parsed = parsed
        self.text = None


class NullCounter:
    def check_and_increment(self) -> int:
        return 1


def _client_returning(parsed) -> tuple[GeminiClient, FakeRunner]:
    runner = FakeRunner(FakeResponse(parsed))
    return GeminiClient(runner, NullCounter()), runner


def _minimal_strength_context() -> StrengthWorkoutContext:
    return StrengthWorkoutContext(
        strava_activity_id=1, title="Push Day A", start_time=datetime(2026, 7, 26), duration_s=3600,
        exercises=[], total_volume_kg=1000, working_set_count=5, total_reps=40, density_kg_per_min=16.7,
        push_pull_ratio=None, upper_lower_ratio=None, avg_rpe=None, avg_rest_seconds=None,
        volume_by_muscle_group=[], personal_records=[],
    )


def _minimal_endurance_context() -> EnduranceWorkoutContext:
    return EnduranceWorkoutContext(
        strava_activity_id=2, sport_type="Run", start_time=datetime(2026, 7, 26), distance_m=8000,
        moving_time_s=2400, elapsed_time_s=2450, avg_hr=150, max_hr_observed=170, elevation_gain_m=50,
        zone_distribution=[], splits=[], pace_cv=None, grade_adjusted_pace_s_per_km=None, decoupling_pct=None,
        efficiency_factor=None, cadence_spm=None, cadence_cv=None, trimp=None, suffer_score=None, zone1_fade_pct=None,
        classification=RunClassification.EASY_RECOVERY, classification_confidence=0.8, classification_evidence=["test"],
    )


def test_orchestrator_routes_successfully():
    parsed = OrchestratorOutput(
        intent=Intent.QUESTION_ABOUT_WORKOUT, target_entity=TargetEntity(type="workout", ref="w1"),
        needs_data=["none"], needs_chart=False, resolved_pronouns=[PronounResolution(pronoun="it", resolved_to="w1")], route_to="conversation",
    )
    gemini, runner = _client_returning(parsed)
    agent = OrchestratorAgent(gemini, model="gemini-3.1-flash-lite")

    result = agent.route(session_context="CURRENT FOCUS: Push Day A", user_message="how was it?")
    assert result.route_to == "conversation"
    assert "how was it?" in runner.models.last_contents


def test_orchestrator_falls_back_to_conversation_on_failure():
    class FailingRunner:
        class models:
            @staticmethod
            def generate_content(**kwargs):
                raise RuntimeError("network exploded")

    gemini = GeminiClient(FailingRunner(), NullCounter())
    agent = OrchestratorAgent(gemini, model="m")

    result = agent.route(session_context="", user_message="hi")
    assert result.route_to == "conversation"
    assert result.intent == Intent.UNCLEAR


def test_strength_analyst_prompt_formats_and_returns_analysis():
    parsed = StrengthAnalysis(headline="Solid push day", what_went_well=["a"], what_to_improve=["b"], next_session="do more")
    gemini, runner = _client_returning(parsed)
    agent = StrengthAnalystAgent(gemini, model="gemini-3.5-flash")

    result = agent.analyze(
        context=_minimal_strength_context(), recent_history_json="[]",
        raw_activity_text="Bench Press (Barbell)\n60kg x 8", training_goal="Hypertrophy",
    )
    assert result.headline == "Solid push day"
    assert "Hypertrophy" in runner.models.last_contents
    assert "Push Day A" in runner.models.last_contents


def test_endurance_analyst_prompt_formats():
    from app.models.agent_io import EnduranceAnalysis

    parsed = EnduranceAnalysis(
        classification=RunClassification.EASY_RECOVERY, classification_confidence=0.8, headline="Easy day",
        execution="on target", what_went_well=["a"], what_to_improve=["b"], next_session="repeat", load_context="fine",
    )
    gemini, runner = _client_returning(parsed)
    agent = EnduranceAnalystAgent(gemini, model="gemini-3.5-flash")

    result = agent.analyze(
        context=_minimal_endurance_context(), recent_history_json="[]", raw_activity_text="", training_goal="Running performance",
    )
    assert result.classification == RunClassification.EASY_RECOVERY


def test_chart_agent_falls_back_to_default_on_invalid_chart_id():
    parsed = ChartSelection(chart_id="totally_made_up", params=[], caption="nice")
    gemini, _ = _client_returning(parsed)
    agent = ChartAgent(gemini, model="m")

    result = agent.select(
        session_context="", available_data_keys_json="[]", trigger_reason="briefing",
        default_chart_id="s01_volume_by_muscle",
    )
    assert result.chart_id == "s01_volume_by_muscle"


def test_chart_agent_accepts_valid_chart_id():
    parsed = ChartSelection(chart_id="e01_hr_zone_distribution", params=[], caption="zones")
    gemini, _ = _client_returning(parsed)
    agent = ChartAgent(gemini, model="m")

    result = agent.select(
        session_context="", available_data_keys_json="[]", trigger_reason="run briefing",
        default_chart_id="s01_volume_by_muscle",
    )
    assert result.chart_id == "e01_hr_zone_distribution"


def test_conversation_agent_prompt_formats():
    parsed = ConversationReply(reply_markdown="**Great session!**", updated_focus=None)
    gemini, runner = _client_returning(parsed)
    agent = ConversationAgent(gemini, model="m")

    result = agent.reply(
        training_goal="Hybrid", session_context="CURRENT FOCUS: Push Day A", recent_turns="[]",
        fetched_data_json="{}", raw_activity_text="", user_message="how was it?",
    )
    assert result.reply_markdown == "**Great session!**"
    assert "how was it?" in runner.models.last_contents
