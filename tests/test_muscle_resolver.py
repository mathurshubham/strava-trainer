"""Tiered muscle-map resolution: static table -> learned Postgres row -> LLM.

The point of the resolver is that the static seed table is allowed to be
incomplete — so these tests care most about what happens on a miss.
"""
from __future__ import annotations

import pytest

from app.metrics.muscle_resolver import ALLOWED_GROUPS, MuscleMapResolver
from app.models.agent_io import ExerciseMuscleClassification


class FakeRepo:
    def __init__(self, rows: dict[str, tuple[list[str], list[str]]] | None = None) -> None:
        self.rows = dict(rows or {})
        self.saves: list[tuple] = []

    def get(self, key):
        return self.rows.get(key)

    def save(self, key, display_name, primary, secondary, *, source="llm"):
        self.saves.append((key, display_name, primary, secondary, source))
        self.rows[key] = (primary, secondary)


class FakeGemini:
    def __init__(self, result, exc: Exception | None = None) -> None:
        self.result = result
        self.exc = exc
        self.calls = 0

    def generate_structured(self, *, model, prompt, response_schema, max_retries=1):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.result


def test_static_table_wins_and_never_calls_the_llm():
    gemini = FakeGemini(None)
    resolver = MuscleMapResolver(FakeRepo(), gemini, "m")
    weights, found = resolver("Bench Press (Barbell)")
    assert found and weights["chest"] == 1.0
    assert gemini.calls == 0


def test_learned_row_is_used_before_the_llm():
    repo = FakeRepo({"iso_lateral_row": (["back"], ["biceps"])})
    gemini = FakeGemini(None)
    resolver = MuscleMapResolver(repo, gemini, "m")
    weights, found = resolver("Iso-Lateral Row (Machine)")
    assert found
    assert weights == {"back": 1.0, "biceps": 0.5}
    assert gemini.calls == 0


def test_unknown_exercise_is_classified_once_and_persisted():
    repo = FakeRepo()
    gemini = FakeGemini(ExerciseMuscleClassification(primary=["triceps"], secondary=["shoulders"], confidence=0.9))
    resolver = MuscleMapResolver(repo, gemini, "m")

    weights, found = resolver("Seated Triceps Press")
    assert found
    assert weights == {"triceps": 1.0, "shoulders": 0.5}
    assert repo.saves == [("seated_triceps_press", "Seated Triceps Press", ["triceps"], ["shoulders"], "llm")]

    # Second lookup of the same exercise must not cost another call.
    resolver("Seated Triceps Press")
    assert gemini.calls == 1


def test_groups_outside_the_allowed_vocabulary_are_dropped():
    """An invented group name would silently skew push/pull and upper/lower ratios."""
    repo = FakeRepo()
    gemini = FakeGemini(
        ExerciseMuscleClassification(primary=["triceps", "long_head"], secondary=["rotator_cuff"], confidence=0.9)
    )
    resolver = MuscleMapResolver(repo, gemini, "m")
    weights, found = resolver("Some Novel Press")
    assert found
    assert weights == {"triceps": 1.0}
    assert set(weights) <= ALLOWED_GROUPS


def test_low_confidence_classification_is_refused():
    repo = FakeRepo()
    gemini = FakeGemini(ExerciseMuscleClassification(primary=["chest"], secondary=[], confidence=0.1))
    resolver = MuscleMapResolver(repo, gemini, "m")
    weights, found = resolver("Gibberish Movement")
    assert (weights, found) == ({}, False)
    assert repo.saves == []


def test_non_exercise_lines_resolve_to_nothing():
    """Hevy footers and stray text must not become phantom muscle volume."""
    repo = FakeRepo()
    gemini = FakeGemini(ExerciseMuscleClassification(primary=[], secondary=[], confidence=0.0))
    resolver = MuscleMapResolver(repo, gemini, "m")
    assert resolver("Logged with hevyapp.com") == ({}, False)
    assert repo.saves == []


def test_llm_failure_degrades_to_not_found_rather_than_guessing():
    from app.clients.gemini import GeminiDailyCapExceeded

    repo = FakeRepo()
    gemini = FakeGemini(None, exc=GeminiDailyCapExceeded("cap"))
    resolver = MuscleMapResolver(repo, gemini, "m")
    assert resolver("Brand New Lift") == ({}, False)


def test_resolver_without_gemini_is_static_plus_learned_only():
    repo = FakeRepo({"my_lift": (["back"], [])})
    resolver = MuscleMapResolver(repo, gemini=None, model=None)
    assert resolver("My Lift")[1] is True
    assert resolver("Totally Unknown Thing") == ({}, False)


def test_repo_failure_does_not_crash_the_briefing():
    class BrokenRepo(FakeRepo):
        def get(self, key):
            raise RuntimeError("postgres down")

    gemini = FakeGemini(ExerciseMuscleClassification(primary=["chest"], secondary=[], confidence=0.9))
    resolver = MuscleMapResolver(BrokenRepo(), gemini, "m")
    weights, found = resolver("Weird Press")
    assert found and weights == {"chest": 1.0}
