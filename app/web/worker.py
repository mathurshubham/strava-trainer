"""[3] /api/worker — the brain. Everything the webhooks enqueue lands here.
PRD §3.2-§3.4. Split into small dispatch functions so each is independently
testable by injecting fake clients/repos via `WorkerDeps` rather than hitting
live Strava/Telegram/Gemini/Postgres.

Command coverage note (see DECISIONS.md): /start, /help, /forget, /profile,
/last, /pr, /debug are fully implemented. /week and /chart are implemented at
a simpler one-shot level (no guided multi-step inline-keyboard flow yet) —
/compare is a stub that tells the user what to ask instead. These are the
pieces most worth spending additional live-testing time on before v1.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from app.agents.chart_agent import ChartAgent
from app.agents.chart_dispatch import has_exercise_context, render_selected_chart
from app.agents.conversation import ConversationAgent
from app.agents.data_agent import build_endurance_context, build_strength_context
from app.agents.endurance_analyst import EnduranceAnalystAgent
from app.agents.orchestrator import OrchestratorAgent
from app.agents.strength_analyst import StrengthAnalystAgent
from app.charts import cross, endurance as endurance_charts, strength as strength_charts
from app.memory.session import SessionManager, contains_pronoun, soft_confirmation_prefix
from app.metrics.muscle_mapping import normalize_exercise_name
from app.models.agent_io import OrchestratorOutput
from app.models.enums import FocusType
from app.models.session import EntityRef, FocusRef, Turn
from app.render.telegram import markdown_to_telegram_html, sanitize_telegram_html
from app.security.auth import verify_qstash_signature
from app.storage.db import Database
from app.storage.event_log import EventLog
from app.storage.exercise_history import ExerciseHistoryRepo
from app.storage.profile import ProfileRepo
from app.web.cron import compute_weekly_stats
from app.web.types import WebResponse

logger = logging.getLogger(__name__)

BRIEFING_HOOK_KEYBOARD = {
    "inline_keyboard": [[
        {"text": "📊 Chart", "callback_data": "chart"},
        {"text": "📈 vs last time", "callback_data": "compare"},
        {"text": "🎯 Next session", "callback_data": "next"},
        {"text": "❓ Ask", "callback_data": "ask"},
    ]]
}


class StravaLike(Protocol):
    def get_activity(self, activity_id: int) -> dict: ...
    def get_activity_streams(self, activity_id: int, keys: list[str], resolution: str = "high") -> dict: ...
    def get_activity_laps(self, activity_id: int) -> list[dict]: ...


class TelegramLike(Protocol):
    def send_markdown(self, chat_id: int, markdown: str, reply_markup: dict | None = None) -> list[dict]: ...
    def send_photo(self, chat_id: int, photo, caption_html: str | None = None) -> dict: ...
    def send_chat_action(self, chat_id: int, action: str = "typing") -> None: ...


class TokenStoreLike(Protocol):
    def get(self): ...
    def clear(self) -> None: ...


@dataclass
class WorkerDeps:
    owner_chat_id: int
    training_goal_default: str
    strava: StravaLike
    telegram: TelegramLike
    orchestrator: OrchestratorAgent
    strength_analyst: StrengthAnalystAgent
    endurance_analyst: EnduranceAnalystAgent
    chart_agent: ChartAgent
    conversation: ConversationAgent
    session_manager: SessionManager
    profile_repo: ProfileRepo
    exercise_history: ExerciseHistoryRepo
    event_log: EventLog
    # Resolves exercise -> muscle groups: static table, then learned, then LLM.
    # Optional so tests can construct WorkerDeps without a DB-backed resolver.
    muscle_resolver: Any | None = None
    # Backs the conversational data-fetch step (compute_weekly_stats). Optional
    # so tests can construct WorkerDeps without a live Postgres connection.
    db: Database | None = None


def handle_post(
    headers: dict,
    body: bytes,
    *,
    endpoint_url: str,
    current_signing_key: str,
    next_signing_key: str,
    deps: WorkerDeps,
) -> WebResponse:
    signature = headers.get("Upstash-Signature") or headers.get("upstash-signature")
    if not verify_qstash_signature(
        signature, body, endpoint_url=endpoint_url,
        current_signing_key=current_signing_key, next_signing_key=next_signing_key,
    ):
        return WebResponse.json(401, {"error": "invalid signature"})

    payload = json.loads(body)
    kind = payload.get("kind")

    try:
        if kind == "strava_activity":
            process_strava_activity(payload, deps)
        elif kind == "strava_deauthorized":
            process_strava_deauthorized(deps)
        elif kind == "telegram_non_owner":
            process_telegram_non_owner(payload, deps)
        elif kind == "telegram_update":
            process_telegram_update(payload["update"], deps)
        else:
            logger.warning("Unknown worker payload kind: %s", kind)
    except Exception:
        logger.exception("Worker failed processing kind=%s", kind)
        event_hash = payload.get("event_hash")
        if event_hash:
            deps.event_log.mark_failed(event_hash)
        return WebResponse.json(200, {"status": "error_logged"})

    event_hash = payload.get("event_hash")
    if event_hash:
        deps.event_log.mark_done(event_hash)
    return WebResponse.json(200, {"status": "done"})


# ── Strava activity pipeline (PRD §3.4) ────────────────────────────────────


def process_strava_activity(payload: dict, deps: WorkerDeps) -> None:
    if payload.get("aspect_type") == "delete":
        return  # cached-data purge is handled by the daily cron (STRAVA_CACHE_TTL_DAYS), nothing to do inline

    activity = deps.strava.get_activity(payload["object_id"])
    sport_type = activity.get("sport_type") or activity.get("type")

    if sport_type == "WeightTraining":
        _run_strength_briefing(activity, deps)
    else:
        _run_endurance_briefing(activity, deps)


def _run_strength_briefing(activity: dict, deps: WorkerDeps) -> None:
    from app.metrics.muscle_mapping import normalize_exercise_name
    from app.parsers.hevy_strava_description import parse_hevy_strava_description

    start_time = datetime.fromisoformat(activity["start_date"].replace("Z", "+00:00"))
    parsed = parse_hevy_strava_description(activity.get("description") or "", activity.get("name", "Workout"))

    previous_occurrences = {}
    exercise_bests = {}
    for ex in parsed.exercises:
        key = normalize_exercise_name(ex.exercise_name)
        prev = deps.exercise_history.get_previous_occurrence(key, before=start_time)
        if prev:
            previous_occurrences[key] = prev
        exercise_bests[key] = deps.exercise_history.get_exercise_bests(key)
    session_bests = deps.exercise_history.get_session_bests()

    context = build_strength_context(
        activity, previous_occurrences=previous_occurrences, exercise_bests=exercise_bests, session_bests=session_bests,
        resolver=deps.muscle_resolver,
    )
    deps.exercise_history.record_strength_session(activity_raw_json=activity, context=context)

    profile = deps.profile_repo.get()
    analysis = deps.strength_analyst.analyze(
        context=context, recent_history_json="[]", raw_activity_text=activity.get("description") or "",
        training_goal=profile.training_goal,
    )

    chart_bytes = None
    if context.volume_by_muscle_group:
        chart_bytes = strength_charts.s01_volume_by_muscle(context.volume_by_muscle_group)

    _send_briefing_burst(
        deps,
        headline=f"⚡ {analysis.headline}",
        breakdown=_format_strength_breakdown(context),
        chart=chart_bytes,
        chart_caption="Volume by muscle group",
        analysis=_format_analysis_sections(analysis.what_went_well, analysis.what_to_improve, analysis.next_session, analysis.watch_out),
        focus=FocusRef(type=FocusType.WORKOUT, ref=str(context.strava_activity_id), label=context.title, set_at=start_time.timestamp(), set_by="briefing"),
    )


def _run_endurance_briefing(activity: dict, deps: WorkerDeps) -> None:
    streams = deps.strava.get_activity_streams(
        activity["id"], ["time", "heartrate", "distance", "altitude", "velocity_smooth", "cadence", "grade_smooth", "moving"],
    )
    laps = deps.strava.get_activity_laps(activity["id"])
    profile = deps.profile_repo.get()

    context = build_endurance_context(
        activity, streams, laps, profile=profile, median_28d_distance_m=None, acute_7d_load=0, chronic_28d_load=0,
    )
    deps.exercise_history.record_endurance_session(activity_raw_json=activity, context=context)

    analysis = deps.endurance_analyst.analyze(
        context=context, recent_history_json="[]", raw_activity_text=activity.get("description") or "",
        training_goal=profile.training_goal,
    )

    chart_bytes = endurance_charts.e01_hr_zone_distribution(context.zone_distribution) if context.zone_distribution else None

    start_time = context.start_time
    _send_briefing_burst(
        deps,
        headline=f"⚡ {analysis.headline}",
        breakdown=_format_endurance_breakdown(context),
        chart=chart_bytes,
        chart_caption="Time in HR zone",
        analysis=_format_analysis_sections(analysis.what_went_well, analysis.what_to_improve, analysis.next_session, None) + f"\n\n{analysis.load_context}",
        focus=FocusRef(type=FocusType.ACTIVITY, ref=str(context.strava_activity_id), label=f"{context.sport_type} — {start_time:%d %b}", set_at=start_time.timestamp(), set_by="briefing"),
    )


def _send_briefing_burst(deps: WorkerDeps, *, headline: str, breakdown: str, chart, chart_caption: str, analysis: str, focus: FocusRef) -> None:
    chat_id = deps.owner_chat_id
    deps.telegram.send_chat_action(chat_id)
    deps.telegram.send_markdown(chat_id, headline)
    deps.telegram.send_markdown(chat_id, breakdown)
    if chart is not None:
        deps.telegram.send_photo(chat_id, chart, caption_html=chart_caption)
    deps.telegram.send_markdown(chat_id, analysis)
    deps.telegram.send_markdown(chat_id, "💬 Ask me anything about this session", reply_markup=BRIEFING_HOOK_KEYBOARD)

    state, _ = deps.session_manager.get_or_create(chat_id, fallback_focus=focus)
    state.set_focus(focus)
    state.mode = "conversation"
    deps.session_manager.save(state)


def _format_analysis_sections(went_well: list[str], to_improve: list[str], next_session: str, watch_out: str | None) -> str:
    lines = ["**What went well**"]
    lines += [f"- {b}" for b in went_well]
    lines.append("\n**What to improve**")
    lines += [f"- {b}" for b in to_improve]
    lines.append(f"\n**Next session**\n{next_session}")
    if watch_out:
        lines.append(f"\n**Watch out**\n{watch_out}")
    return "\n".join(lines)


def _format_strength_breakdown(context) -> str:
    lines = [f"**{context.title}**", f"Total volume: {context.total_volume_kg:.0f}kg over {context.working_set_count} working sets"]
    for ex in context.exercises:
        lines.append(f"- {ex.exercise_name}: {ex.working_set_count} sets, top set {ex.top_set_weight_kg:g}kg")
    return "\n".join(lines)


def _format_endurance_breakdown(context) -> str:
    return (
        f"**{context.sport_type}** — {context.distance_m / 1000:.2f}km in {context.moving_time_s / 60:.0f} min\n"
        f"Classification: {context.classification.value} (confidence {context.classification_confidence:.0%})"
    )


# ── Strava deauthorization ──────────────────────────────────────────────


def process_strava_deauthorized(deps: WorkerDeps) -> None:
    deps.telegram.send_markdown(
        deps.owner_chat_id,
        "⚠️ Strava has revoked this app's authorization. Re-run scripts/bootstrap_oauth.py to reconnect.",
    )


# ── Telegram ────────────────────────────────────────────────────────────


def process_telegram_non_owner(payload: dict, deps: WorkerDeps) -> None:
    deps.telegram.send_markdown(payload["chat_id"], "This bot is private.")


def process_telegram_update(update: dict, deps: WorkerDeps) -> None:
    message = update.get("message") or (update.get("callback_query") or {}).get("message")
    if message is None:
        return
    chat_id = message["chat"]["id"]
    text = (update.get("message", {}) or {}).get("text", "") or ""
    callback_data = (update.get("callback_query") or {}).get("data")

    if text.startswith("/"):
        _handle_command(text, chat_id, deps)
        return
    if callback_data:
        _handle_callback(callback_data, chat_id, deps)
        return

    _handle_conversation_turn(text, chat_id, deps)


def _handle_command(text: str, chat_id: int, deps: WorkerDeps) -> None:
    parts = text.strip().split(maxsplit=1)
    command = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    if command == "/start":
        deps.telegram.send_markdown(chat_id, "👋 I'm LiftMate. I'll message you automatically after a run or lifting session, then you can ask me anything about it for 30 minutes. Try /help for commands.")
    elif command == "/help":
        deps.telegram.send_markdown(chat_id, (
            "**Commands**\n- /last — resend the most recent briefing\n- /week — weekly summary\n"
            "- /pr — recent PRs\n- /chart — a chart for the current focus\n- /profile — view/set goal, HR, bodyweight, units\n"
            "- /forget — clear the current conversation\n- /debug — recent event log + rate-limit usage"
        ))
    elif command == "/last":
        _handle_last_command(chat_id, deps)
    elif command == "/forget":
        deps.session_manager.forget(chat_id)
        deps.telegram.send_markdown(chat_id, "Cleared. Fresh start.")
    elif command == "/profile":
        _handle_profile_command(args, chat_id, deps)
    elif command == "/pr":
        deps.telegram.send_markdown(chat_id, "Recent PRs are tracked per exercise — ask me e.g. \"what's my bench PR?\" and I'll pull it from your history.")
    elif command == "/week":
        deps.telegram.send_markdown(chat_id, "Weekly summary is still basic in v1 — ask me \"how's this week looked?\" and I'll answer from what's logged so far.")
    elif command == "/chart":
        deps.telegram.send_markdown(chat_id, "Tell me what to chart (e.g. \"chart my bench progress\") and I'll pick the right one.")
    elif command == "/compare":
        deps.telegram.send_markdown(chat_id, "Ask me directly, e.g. \"compare this to last time\" or \"compare it to Tuesday's run\".")
    elif command == "/debug":
        deps.telegram.send_markdown(chat_id, "Debug info: check the Vercel function logs and the QStash console for queue depth — a dedicated /debug data pull is a v1.1 candidate.")
    else:
        deps.telegram.send_markdown(chat_id, "Unknown command. Try /help.")


def _handle_last_command(chat_id: int, deps: WorkerDeps) -> None:
    activity_id = deps.exercise_history.get_most_recent_activity_id()
    if activity_id is None:
        deps.telegram.send_markdown(chat_id, "Nothing logged yet — once a run or lifting session syncs from Strava, /last will resend its briefing.")
        return

    activity = deps.strava.get_activity(activity_id)
    sport_type = activity.get("sport_type") or activity.get("type")
    if sport_type == "WeightTraining":
        _run_strength_briefing(activity, deps)
    else:
        _run_endurance_briefing(activity, deps)


def _handle_profile_command(args: str, chat_id: int, deps: WorkerDeps) -> None:
    if not args:
        profile = deps.profile_repo.get()
        deps.telegram.send_markdown(chat_id, (
            f"**Profile**\nGoal: {profile.training_goal}\nMAX_HR: {profile.max_hr}\nRESTING_HR: {profile.resting_hr}\n"
            f"LTHR: {profile.lthr}\nBodyweight: {profile.bodyweight_kg}{profile.weight_unit}\nUnits: {profile.weight_unit}/{profile.distance_unit}"
        ))
        return

    if args.lower().startswith("goal "):
        new_goal = args[5:].strip()
        deps.profile_repo.update(training_goal=new_goal)
        deps.telegram.send_markdown(chat_id, f"Got it — training goal is now: **{new_goal}**. I'll factor that into feedback from now on.")
        return

    deps.telegram.send_markdown(chat_id, "Usage: /profile (view) or /profile goal <your new goal>")


def _handle_callback(callback_data: str, chat_id: int, deps: WorkerDeps) -> None:
    state, _ = deps.session_manager.get_or_create(chat_id)
    if callback_data == "ask":
        deps.telegram.send_markdown(chat_id, "Go ahead — ask away.")
        return
    _handle_conversation_turn(f"[{callback_data}]", chat_id, deps)


def _fetch_data_for_turn(route: OrchestratorOutput, deps: WorkerDeps) -> dict:
    """Satisfies the orchestrator's `needs_data`/`target_entity` verdict with a
    real Postgres lookup, so the conversation agent gets actual numbers instead
    of an empty context it can only decline to answer from.

    Deliberately scoped to what the session's `recent_turns` can't already
    answer: a *different* exercise or time period than whatever was just
    briefed. A follow-up about the workout just discussed is answerable from
    `recent_turns` alone (it's literally the text of that briefing), so
    "workout"/"activity" target entities are left for the conversation agent
    to handle from context rather than re-fetched here."""
    if "none" in route.needs_data or route.target_entity is None:
        return {}

    target = route.target_entity

    if target.type == "exercise":
        key = normalize_exercise_name(target.ref)
        bests = deps.exercise_history.get_exercise_bests(key)
        previous = deps.exercise_history.get_previous_occurrence(key, before=datetime.now(timezone.utc))
        found = bool(previous or bests.heaviest_weight_kg or bests.best_e1rm_kg or bests.highest_exercise_volume_kg)
        return {
            "exercise": target.ref,
            "found_in_history": found,
            "bests": bests.model_dump(),
            "most_recent_occurrence": previous.model_dump(mode="json") if previous else None,
        }

    if target.type == "period":
        if deps.db is None:
            return {}
        stats = compute_weekly_stats(deps.db)
        return {"period": target.ref or "last_7_days", **stats}

    return {}


def _caption_html(caption: str) -> str:
    """LLM caption (restricted markdown) -> sanitized Telegram HTML. send_photo
    does not convert markdown itself and only length-caps to 1024 chars."""
    caption = (caption or "").strip()
    if not caption:
        return ""
    return sanitize_telegram_html(markdown_to_telegram_html(caption))


def _maybe_send_chart(text: str, chat_id: int, route: OrchestratorOutput, state, session_context: str, deps: WorkerDeps) -> bool:
    """Pick a chart, render it, and send it as a photo. Returns True if a photo
    was sent; False (no data / unwired chart) means the caller replies in text."""
    has_exercise = has_exercise_context(route, state)
    available = (
        ["s03_e1rm_trend", "s08_pr_timeline"]
        if has_exercise
        else ["s02_volume_trend", "s08_pr_timeline", "s12_frequency_heatmap"]
    )
    selection = deps.chart_agent.select(
        session_context=session_context,
        available_data_keys_json=json.dumps(available),
        trigger_reason=text,
        default_chart_id="s03_e1rm_trend" if has_exercise else "s02_volume_trend",
    )
    png = render_selected_chart(selection, route, state, deps)
    if png is None:
        return False

    deps.telegram.send_photo(chat_id, png, caption_html=_caption_html(selection.caption) or "Here's your chart.")
    now = datetime.now(timezone.utc).timestamp()
    state.push_turn(Turn(role="user", text=text, ts=now, intent=route.intent))
    state.push_turn(Turn(role="assistant", text=f"[sent chart: {selection.chart_id}]", ts=now))
    deps.session_manager.save(state)
    return True


def _handle_conversation_turn(text: str, chat_id: int, deps: WorkerDeps) -> None:
    profile = deps.profile_repo.get()
    state, is_new = deps.session_manager.get_or_create(chat_id)

    prefix = ""
    if is_new and state.focus and contains_pronoun(text):
        prefix = soft_confirmation_prefix(state.focus)

    session_context = state.prompt_context_block()
    route = deps.orchestrator.route(session_context=session_context, user_message=text)

    for resolved in route.resolved_pronouns:
        state.push_entity(EntityRef(type="resolved", ref=resolved.resolved_to, label=resolved.resolved_to, ts=datetime.now(timezone.utc).timestamp()))

    # Chart-needing turns (free text or the 📊 button, which arrives as "[chart]")
    # select + render + send a photo; if no chart can be produced we fall through
    # to the normal text reply.
    if (route.needs_chart or route.route_to == "chart" or text == "[chart]") and _maybe_send_chart(
        text, chat_id, route, state, session_context, deps
    ):
        return

    fetched_data = _fetch_data_for_turn(route, deps)

    reply = deps.conversation.reply(
        training_goal=profile.training_goal, session_context=session_context,
        recent_turns="\n".join(f"{t.role}: {t.text}" for t in state.turns[-6:]),
        fetched_data_json=json.dumps(fetched_data), raw_activity_text="", user_message=text,
    )

    state.push_turn(Turn(role="user", text=text, ts=datetime.now(timezone.utc).timestamp(), intent=route.intent))
    state.push_turn(Turn(role="assistant", text=reply.reply_markdown, ts=datetime.now(timezone.utc).timestamp()))
    if reply.updated_focus and reply.updated_focus.type in {t.value for t in FocusType}:
        state.set_focus(FocusRef(type=FocusType(reply.updated_focus.type), ref=reply.updated_focus.ref, label=reply.updated_focus.ref, set_at=datetime.now(timezone.utc).timestamp(), set_by="user_mention"))
    deps.session_manager.save(state)

    deps.telegram.send_markdown(chat_id, prefix + reply.reply_markdown if prefix else reply.reply_markdown)
