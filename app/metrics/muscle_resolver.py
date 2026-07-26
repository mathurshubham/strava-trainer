"""[10] Muscle-map resolver — three-tier lookup for exercise -> muscle groups.

The static seed table (`muscle_mapping.json`) can only ever cover the exercises
we thought of at build time. Rather than hand-curating it every time a new
movement shows up in Hevy — which guarantees it drifts out of date and silently
drops that exercise's volume from muscle-group totals — unknown exercises are
classified once by the LLM and cached in Postgres.

Lookup order, cheapest first:

    1. static JSON table    — no I/O, covers the common vocabulary
    2. learned Postgres row — one query, covers everything seen before
    3. LLM classification   — one call, ever, per new exercise; result persisted

If the LLM tier is unavailable (no resolver wired in, daily cap hit, bad output)
this degrades to exactly the old behaviour: `found=False`, and the caller records
a parser warning instead of inventing numbers.
"""
from __future__ import annotations

import logging

from app.clients.gemini import GeminiClient, GeminiDailyCapExceeded, GeminiStructuredOutputError
from app.metrics.muscle_mapping import (
    LOWER_GROUPS,
    UPPER_GROUPS,
    muscle_groups_for,
    normalize_exercise_name,
)
from app.models.agent_io import ExerciseMuscleClassification
from app.prompts.loader import load_prompt
from app.storage.muscle_map import LearnedMuscleMapRepo

logger = logging.getLogger(__name__)

# The vocabulary the metrics engine can actually aggregate. Anything the model
# returns outside this set is dropped — push/pull and upper/lower ratios are
# computed from these names, so an invented group would silently skew them.
ALLOWED_GROUPS = UPPER_GROUPS | LOWER_GROUPS | {"core", "adductors", "abductors", "neck"}

MIN_CONFIDENCE = 0.4


def _weights(primary: list[str], secondary: list[str]) -> dict[str, float]:
    weights: dict[str, float] = {}
    for group in primary:
        weights[group] = max(weights.get(group, 0.0), 1.0)
    for group in secondary:
        weights[group] = max(weights.get(group, 0.0), 0.5)
    return weights


class MuscleMapResolver:
    def __init__(
        self,
        repo: LearnedMuscleMapRepo,
        gemini: GeminiClient | None = None,
        model: str | None = None,
    ) -> None:
        self._repo = repo
        self._gemini = gemini
        self._model = model
        self._session_cache: dict[str, tuple[dict[str, float], bool]] = {}

    def __call__(self, exercise_name: str) -> tuple[dict[str, float], bool]:
        """Same signature as `muscle_groups_for`, so it drops straight into the
        metrics engine as a callable."""
        key = normalize_exercise_name(exercise_name)
        if key in self._session_cache:
            return self._session_cache[key]

        result = self._resolve(key, exercise_name)
        self._session_cache[key] = result
        return result

    def _resolve(self, key: str, display_name: str) -> tuple[dict[str, float], bool]:
        # Tier 1: static table.
        weights, found = muscle_groups_for(display_name)
        if found:
            return weights, True

        # Tier 2: previously learned.
        try:
            learned = self._repo.get(key)
        except Exception:
            logger.exception("Learned muscle-map lookup failed for %r; falling through to LLM.", key)
            learned = None
        if learned is not None:
            primary, secondary = learned
            return _weights(primary, secondary), bool(primary or secondary)

        # Tier 3: classify once, then persist.
        classified = self._classify(display_name)
        if classified is None:
            return {}, False

        primary, secondary = classified
        try:
            self._repo.save(key, display_name, primary, secondary, source="llm")
        except Exception:
            # A failed write costs one repeat classification next time, nothing worse.
            logger.exception("Could not persist learned muscle map for %r.", key)

        return _weights(primary, secondary), bool(primary or secondary)

    def _classify(self, display_name: str) -> tuple[list[str], list[str]] | None:
        if self._gemini is None or self._model is None:
            return None

        prompt = load_prompt(
            "muscle_resolver",
            exercise_name=display_name,
            allowed_groups="\n".join(f"- {g}" for g in sorted(ALLOWED_GROUPS)),
        )
        try:
            result = self._gemini.generate_structured(
                model=self._model, prompt=prompt, response_schema=ExerciseMuscleClassification
            )
        except (GeminiStructuredOutputError, GeminiDailyCapExceeded):
            logger.warning("Muscle classification unavailable for %r.", display_name)
            return None

        if result.confidence < MIN_CONFIDENCE:
            logger.info("Muscle classification for %r below confidence floor (%.2f).", display_name, result.confidence)
            return None

        primary = [g for g in result.primary if g in ALLOWED_GROUPS]
        secondary = [g for g in result.secondary if g in ALLOWED_GROUPS and g not in primary]
        if not primary and not secondary:
            return None

        logger.info("Learned muscle map: %r -> primary=%s secondary=%s", display_name, primary, secondary)
        return primary, secondary
