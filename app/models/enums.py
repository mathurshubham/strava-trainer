from __future__ import annotations

from enum import StrEnum


class SetType(StrEnum):
    NORMAL = "normal"
    WARMUP = "warmup"
    FAILURE = "failure"
    DROPSET = "dropset"


class RepRange(StrEnum):
    STRENGTH = "strength"  # 1-5
    HYPERTROPHY = "hypertrophy"  # 6-12
    ENDURANCE = "endurance"  # 13+


class RunClassification(StrEnum):
    EASY_RECOVERY = "easy_recovery"
    LONG_RUN = "long_run"
    TEMPO = "tempo"
    THRESHOLD = "threshold"
    INTERVALS = "intervals"
    PROGRESSION = "progression"
    RACE = "race"
    UNCLASSIFIED = "unclassified"


class RecordType(StrEnum):
    HEAVIEST_WEIGHT = "heaviest_weight"
    BEST_E1RM = "best_e1rm"
    MOST_REPS_AT_WEIGHT = "most_reps_at_weight"
    HIGHEST_EXERCISE_VOLUME = "highest_exercise_volume"
    HIGHEST_SESSION_VOLUME = "highest_session_volume"


class FocusType(StrEnum):
    WORKOUT = "workout"
    ACTIVITY = "activity"
    EXERCISE = "exercise"
    PERIOD = "period"


class Intent(StrEnum):
    BRIEFING = "briefing"
    QUESTION_ABOUT_WORKOUT = "question_about_workout"
    COMPARISON = "comparison"
    CHART_REQUEST = "chart_request"
    HISTORY_QUERY = "history_query"
    GENERAL_FITNESS = "general_fitness"
    GREETING = "greeting"
    COMMAND = "command"
    UNCLEAR = "unclear"
