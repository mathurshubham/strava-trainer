"""[4] Orchestrator — LLM (Flash-Lite), cheap and fast. Classifies the turn
and routes it. PRD §3.3. Falls back to `conversation` if structured output
can't be produced even after the Gemini client's own retry."""
from __future__ import annotations

from app.clients.gemini import GeminiClient
from app.models.agent_io import OrchestratorOutput, TargetEntity
from app.models.enums import Intent
from app.prompts.loader import load_prompt


class OrchestratorAgent:
    def __init__(self, gemini: GeminiClient, model: str) -> None:
        self._gemini = gemini
        self._model = model

    def route(self, *, session_context: str, user_message: str) -> OrchestratorOutput:
        """Never raises — this sits at the top of the routing chain, so any
        failure (invalid structured output after retry, a Gemini timeout, a
        transport error) degrades to the same safe default: hand the full
        session context to the conversation agent and let it cope (PRD §3.3)."""
        prompt = load_prompt("orchestrator", session_context=session_context, user_message=user_message)
        try:
            return self._gemini.generate_structured(model=self._model, prompt=prompt, response_schema=OrchestratorOutput)
        except Exception:
            return OrchestratorOutput(
                intent=Intent.UNCLEAR,
                target_entity=None,
                needs_data=["none"],
                needs_chart=False,
                resolved_pronouns=[],
                route_to="conversation",
            )
