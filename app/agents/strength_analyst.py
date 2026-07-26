"""[7] Strength Analyst — LLM. Never sees raw API JSON, only the computed
StrengthWorkoutContext. PRD §3.3, §4.1 output contract."""
from __future__ import annotations

from app.clients.gemini import GeminiClient
from app.models.agent_io import StrengthAnalysis
from app.models.workout import StrengthWorkoutContext
from app.prompts.loader import load_prompt


class StrengthAnalystAgent:
    def __init__(self, gemini: GeminiClient, model: str) -> None:
        self._gemini = gemini
        self._model = model

    def analyze(
        self,
        *,
        context: StrengthWorkoutContext,
        recent_history_json: str,
        raw_activity_text: str,
        training_goal: str,
    ) -> StrengthAnalysis:
        prompt = load_prompt(
            "strength_analyst",
            training_goal=training_goal,
            workout_context_json=context.model_dump_json(indent=2),
            recent_history_json=recent_history_json,
            raw_activity_text=raw_activity_text[:2000],  # PRD §8.3: cap untrusted free text at 2,000 chars
        )
        return self._gemini.generate_structured(model=self._model, prompt=prompt, response_schema=StrengthAnalysis, max_retries=1)
