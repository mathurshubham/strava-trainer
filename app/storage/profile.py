"""Single-row athlete profile (PRD §1.2, §12.7) — mutable via /profile,
notably the training goal, since the developer wants to be able to tell the
bot "my goal is X now" and have it acknowledge and use that going forward."""
from __future__ import annotations

from app.models.profile import AthleteProfile
from app.storage.db import Database

_COLUMNS = "max_hr, resting_hr, lthr, bodyweight_kg, weight_unit, distance_unit, training_goal"


class ProfileRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self) -> AthleteProfile:
        with self._db.connection() as conn:
            row = conn.execute(f"SELECT {_COLUMNS} FROM athlete_profile WHERE id = 1").fetchone()
        max_hr, resting_hr, lthr, bodyweight_kg, weight_unit, distance_unit, training_goal = row
        return AthleteProfile(
            max_hr=max_hr, resting_hr=resting_hr, lthr=lthr, bodyweight_kg=bodyweight_kg,
            weight_unit=weight_unit, distance_unit=distance_unit, training_goal=training_goal,
        )

    def update(self, **fields) -> AthleteProfile:
        """Partial update — pass only the fields changing, e.g. update(training_goal='Strength')."""
        allowed = {"max_hr", "resting_hr", "lthr", "bodyweight_kg", "weight_unit", "distance_unit", "training_goal"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown profile field(s): {unknown}")

        set_clause = ", ".join(f"{k} = %s" for k in fields)
        with self._db.connection() as conn:
            conn.execute(
                f"UPDATE athlete_profile SET {set_clause}, updated_at = now() WHERE id = 1",
                tuple(fields.values()),
            )
        return self.get()
