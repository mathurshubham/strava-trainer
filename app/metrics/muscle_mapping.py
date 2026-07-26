"""Exercise name -> muscle group mapping, loaded from the editable JSON table.

This exists because Strava's activity description (our only lifting data
source per the developer's decision — see DECISIONS.md) has no muscle-group
metadata, unlike Hevy's `exercise_templates` endpoint. We maintain our own
static table instead of calling Hevy.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_MAPPING_PATH = Path(__file__).parent / "muscle_mapping.json"

PUSH_GROUPS = {"chest", "shoulders", "triceps"}
PULL_GROUPS = {"back", "biceps", "traps"}
UPPER_GROUPS = {"chest", "back", "shoulders", "biceps", "triceps", "traps", "forearms"}
LOWER_GROUPS = {"quads", "hamstrings", "glutes", "calves"}


@lru_cache
def _load_mapping() -> dict[str, dict[str, list[str]]]:
    data = json.loads(_MAPPING_PATH.read_text())
    data.pop("_comment", None)
    return data


def normalize_exercise_name(name: str) -> str:
    """"Bench Press (Barbell)" -> "bench_press". Strips equipment/parens, lowercases, snake_cases."""
    without_parens = re.sub(r"\([^)]*\)", "", name)
    lowered = without_parens.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    return slug


# Hevy spells the same movement several ways ("Triceps Rope Pushdown" vs our
# "tricep_pushdown"). Rather than enumerate every spelling in the table, collapse
# the two recurring sources of drift: plural muscle names, and qualifier words
# that describe *how* a lift is done without changing which muscles it trains.
_SYNONYMS = {"triceps": "tricep", "biceps": "bicep", "quadriceps": "quad", "abs": "core"}

# Deliberately excludes words that DO change the muscle split — "incline",
# "decline", "close_grip", "reverse", "front", "rear", "overhead", "sumo".
_QUALIFIERS = (
    "single_arm", "one_arm", "alternating", "alternate", "iso_lateral", "isolateral",
    "seated", "standing", "lying", "bent_over", "rope", "bar", "v_bar", "straight_bar",
    "smith", "assisted", "banded", "kneeling",
)


def _synonym_variants(key: str) -> list[str]:
    """Progressively looser spellings of `key`, most-specific first."""
    tokens = key.split("_")
    canonical = [_SYNONYMS.get(t, t) for t in tokens]
    variants = ["_".join(canonical)]

    stripped = [t for t in canonical if t not in _QUALIFIERS]
    if stripped and stripped != canonical:
        variants.append("_".join(stripped))
    return variants


def muscle_groups_for(exercise_name: str) -> tuple[dict[str, float], bool]:
    """Returns ({muscle_group: weight}, found). weight is 1.0 primary / 0.5 secondary.

    `found=False` means the exercise wasn't in the table — caller should log a
    parser_warning rather than silently dropping the volume.

    Lookup is exact-match first, then falls back to synonym/qualifier-normalised
    spellings so a new Hevy phrasing of a movement we already know doesn't have
    its volume dropped. Anything still unmatched returns found=False.
    """
    mapping = _load_mapping()
    key = normalize_exercise_name(exercise_name)
    entry = mapping.get(key)
    if entry is None:
        for variant in _synonym_variants(key):
            entry = mapping.get(variant)
            if entry is not None:
                break
    if entry is None:
        return {}, False

    weights: dict[str, float] = {}
    for group in entry.get("primary", []):
        weights[group] = max(weights.get(group, 0.0), 1.0)
    for group in entry.get("secondary", []):
        weights[group] = max(weights.get(group, 0.0), 0.5)
    return weights, True


def known_exercise_keys() -> set[str]:
    return set(_load_mapping().keys())
