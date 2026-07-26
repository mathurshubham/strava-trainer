"""[10] Conversation Agent — LLM. Handles Mode B: one message, single reply.
Hard-forbidden from re-dumping the full breakdown unless asked. PRD §3.3."""
from __future__ import annotations

from app.clients.gemini import GeminiClient
from app.models.agent_io import ConversationReply
from app.prompts.loader import load_prompt


class ConversationAgent:
    def __init__(self, gemini: GeminiClient, model: str) -> None:
        self._gemini = gemini
        self._model = model

    def reply(
        self,
        *,
        training_goal: str,
        session_context: str,
        recent_turns: str,
        fetched_data_json: str,
        raw_activity_text: str,
        user_message: str,
    ) -> ConversationReply:
        prompt = load_prompt(
            "conversation",
            training_goal=training_goal,
            session_context=session_context,
            recent_turns=recent_turns,
            fetched_data_json=fetched_data_json,
            raw_activity_text=raw_activity_text[:2000],
            user_message=user_message,
        )
        return self._gemini.generate_structured(model=self._model, prompt=prompt, response_schema=ConversationReply, max_retries=1)
