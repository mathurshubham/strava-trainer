"""Persistence for LLM-learned exercise -> muscle-group mappings.

The static `app/metrics/muscle_mapping.json` seed table can't keep up with a
growing exercise vocabulary (see DECISIONS.md). This repo stores the mappings
resolved at runtime so each unknown exercise is classified once, ever.
"""
from __future__ import annotations

from app.storage.db import Database


class LearnedMuscleMapRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, exercise_key: str) -> tuple[list[str], list[str]] | None:
        """Returns (primary_groups, secondary_groups), or None if not learned yet."""
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT primary_groups, secondary_groups FROM learned_exercise_muscles WHERE exercise_key = %s",
                (exercise_key,),
            ).fetchone()
        if row is None:
            return None
        return list(row[0]), list(row[1])

    def save(
        self,
        exercise_key: str,
        display_name: str,
        primary_groups: list[str],
        secondary_groups: list[str],
        *,
        source: str = "llm",
    ) -> None:
        """Upsert a mapping. An 'llm' result never overwrites a 'manual' one —
        a human correction is the more trustworthy record and must stick."""
        with self._db.connection() as conn:
            conn.execute(
                """
                INSERT INTO learned_exercise_muscles
                    (exercise_key, display_name, primary_groups, secondary_groups, source)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (exercise_key) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    primary_groups = EXCLUDED.primary_groups,
                    secondary_groups = EXCLUDED.secondary_groups,
                    source = EXCLUDED.source,
                    updated_at = now()
                WHERE learned_exercise_muscles.source != 'manual'
                   OR EXCLUDED.source = 'manual'
                """,
                (exercise_key, display_name, primary_groups, secondary_groups, source),
            )

    def all(self) -> list[tuple[str, str, list[str], list[str], str]]:
        """Every learned mapping, for `/muscles` to display."""
        with self._db.connection() as conn:
            rows = conn.execute(
                """
                SELECT exercise_key, display_name, primary_groups, secondary_groups, source
                FROM learned_exercise_muscles ORDER BY display_name
                """
            ).fetchall()
        return [(k, d, list(p), list(s), src) for k, d, p, s, src in rows]
