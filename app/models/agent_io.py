"""Structured input/output contracts for every LLM-backed agent.

Passed directly as `response_schema` to the Gemini client so the model is
constrained to valid JSON — PRD §3.3: "Constrain with structured output /
response schema — do not parse free text."
"""
from __future__ import annotations

from pydantic import BaseModel

from app.models.enums import Intent, RunClassification


class TargetEntity(BaseModel):
    type: str  # "workout" | "activity" | "exercise" | "period" | "null"
    ref: str


class PronounResolution(BaseModel):
    pronoun: str
    resolved_to: str


class OrchestratorOutput(BaseModel):
    intent: Intent
    target_entity: TargetEntity | None
    needs_data: list[str]  # "hevy_workout" is legacy naming in the PRD; we use "strava_activity" | "exercise_history" | "none"
    needs_chart: bool
    # A list of {pronoun, resolved_to}, NOT dict[str, str] — Gemini's Developer
    # API structured-output mode rejects any schema needing `additionalProperties`
    # (arbitrary-key maps aren't representable in its constrained JSON schema
    # subset) and raises ValueError at the request-building step, before any
    # network call. That exception was being swallowed by this agent's own
    # broad `except Exception` fallback, so the orchestrator silently never
    # actually ran — see DECISIONS.md, 2026-07-26 "dict[str,str] response
    # schema" entry. list[PronounResolution] renders as an array-of-objects
    # schema, which Gemini's controlled generation supports fine.
    resolved_pronouns: list[PronounResolution]
    route_to: str  # "strength_analyst" | "endurance_analyst" | "conversation" | "chart"


class ChartParam(BaseModel):
    key: str
    value: str


class ChartSelection(BaseModel):
    chart_id: str
    params: list[ChartParam]  # see OrchestratorOutput.resolved_pronouns — same dict[str,str]-is-unsupported reason
    caption: str


class StrengthAnalysis(BaseModel):
    headline: str
    what_went_well: list[str]
    what_to_improve: list[str]
    next_session: str
    watch_out: str | None = None


class EnduranceAnalysis(BaseModel):
    classification: RunClassification
    classification_confidence: float
    headline: str
    execution: str
    what_went_well: list[str]
    what_to_improve: list[str]
    next_session: str
    load_context: str


class ConversationReply(BaseModel):
    reply_markdown: str  # restricted markdown subset — converted to Telegram HTML deterministically
    updated_focus: TargetEntity | None = None


class ExerciseMuscleClassification(BaseModel):
    """[10] Muscle-map resolver — classifies an exercise the static table doesn't know.

    Groups are constrained by the prompt to the fixed vocabulary the metrics
    engine understands; the resolver drops anything outside it rather than
    trusting the model to have obeyed.
    """
    primary: list[str]
    secondary: list[str]
    confidence: float
