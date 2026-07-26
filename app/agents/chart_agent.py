"""[9] Chart Agent — LLM picks from a locked-down enum, Python renders.
PRD §3.3, §6.2. The model never writes plotting code."""
from __future__ import annotations

from app.clients.gemini import GeminiClient, GeminiDailyCapExceeded, GeminiStructuredOutputError
from app.models.agent_io import ChartSelection
from app.prompts.loader import load_prompt

VALID_CHART_IDS = {
    "s01_volume_by_muscle", "s02_volume_trend", "s03_e1rm_trend", "s04_set_breakdown",
    "s05_session_vs_last", "s06_weekly_sets_vs_target", "s07_rep_range_mix", "s08_pr_timeline",
    "s09_load_reps_scatter", "s10_muscle_balance_radar", "s11_intensity_distribution", "s12_frequency_heatmap",
    "e01_hr_zone_distribution", "e02_hr_over_time_zoned", "e03_pace_hr_dual", "e04_splits_bar",
    "e05_decoupling", "e06_run_overlay", "e07_weekly_volume_zones", "e08_zone_mix_28d",
    "e09_cadence_over_time", "e10_elevation_hr",
    "x01_training_load_acwr", "x02_consistency_calendar",
}


class InvalidChartSelection(ValueError):
    pass


class ChartAgent:
    def __init__(self, gemini: GeminiClient, model: str) -> None:
        self._gemini = gemini
        self._model = model

    def select(self, *, session_context: str, available_data_keys_json: str, trigger_reason: str, default_chart_id: str) -> ChartSelection:
        prompt = load_prompt(
            "chart_agent",
            session_context=session_context,
            available_data_keys_json=available_data_keys_json,
            trigger_reason=trigger_reason,
        )
        try:
            selection = self._gemini.generate_structured(model=self._model, prompt=prompt, response_schema=ChartSelection)
        except (GeminiStructuredOutputError, GeminiDailyCapExceeded):
            return ChartSelection(chart_id=default_chart_id, params=[], caption="")

        if selection.chart_id not in VALID_CHART_IDS:
            # Never invented by us — this only fires if the model ignored the catalogue.
            return ChartSelection(chart_id=default_chart_id, params=[], caption=selection.caption)
        return selection
