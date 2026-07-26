"""Single-row athlete profile. Single-tenant app — no user table, just this. PRD §1.2, §12.7."""
from __future__ import annotations

from pydantic import BaseModel


class AthleteProfile(BaseModel):
    max_hr: int | None = None
    resting_hr: int | None = None
    lthr: int | None = None
    bodyweight_kg: float | None = None
    weight_unit: str = "kg"
    distance_unit: str = "km"
    training_goal: str = "Hybrid — lifting + running, general fitness"

    def zone_source(self) -> str:
        """PRD §4.2: priority is Strava custom zones > MAX_HR/RESTING_HR/LTHR > age estimate."""
        if self.lthr:
            return "lthr"
        if self.max_hr:
            return "max_hr"
        return "estimate"
