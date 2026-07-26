"""Loads prompt templates from plain .md files — PRD explicitly wants prompts
versioned as files, never inline strings in agent code (repo layout §9)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


@lru_cache
def _read(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.md").read_text()


def load_prompt(name: str, **placeholders: str) -> str:
    """`name` is the file stem, e.g. load_prompt("strength_analyst", training_goal=..., workout_context_json=...)."""
    return _read(name).format(**placeholders)
