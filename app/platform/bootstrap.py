"""Wires real implementations together from Settings. This is the one place
that constructs live clients — everything it builds satisfies the Protocols
used elsewhere (StravaClient -> StravaLike, TelegramClient -> TelegramLike,
etc.), which is what makes every other module testable with fakes instead.

Deliberately NOT unit tested: it only does construction/wiring, and doing
anything with it requires live credentials (DATABASE_URL, Redis, Strava,
Telegram, Gemini all reachable). Exercise this by actually running the app
once .env is filled in, not by mocking every client here.
"""
from __future__ import annotations

from app.agents.chart_agent import ChartAgent
from app.agents.conversation import ConversationAgent
from app.agents.endurance_analyst import EnduranceAnalystAgent
from app.agents.orchestrator import OrchestratorAgent
from app.agents.strength_analyst import StrengthAnalystAgent
from app.clients.gemini import DailyCallCounter, GeminiClient
from app.clients.strava import StravaClient
from app.clients.telegram import TelegramClient
from app.config import Settings
from app.memory.session import SessionManager
from app.security.crypto import TokenCipher
from app.storage.db import Database
from app.storage.event_log import EventLog
from app.metrics.muscle_resolver import MuscleMapResolver
from app.storage.exercise_history import ExerciseHistoryRepo
from app.storage.muscle_map import LearnedMuscleMapRepo
from app.storage.oauth_tokens import PostgresTokenStore
from app.storage.profile import ProfileRepo
from app.storage.queue import QStashQueue
from app.storage.redis import UpstashRedis
from app.storage.redis_lock import RedisLock
from app.web.worker import WorkerDeps


def build_database(settings: Settings) -> Database:
    return Database(settings.database_url)


def build_redis(settings: Settings) -> UpstashRedis:
    return UpstashRedis(settings.upstash_redis_rest_url, settings.upstash_redis_rest_token)


def build_queue(settings: Settings) -> QStashQueue:
    return QStashQueue(settings.qstash_token)


def build_strava_client(settings: Settings, db: Database, redis: UpstashRedis) -> StravaClient:
    cipher = TokenCipher(settings.encryption_key)
    token_store = PostgresTokenStore(db, cipher)
    return StravaClient(
        client_id=settings.strava_client_id,
        client_secret=settings.strava_client_secret,
        api_base=settings.strava_api_base,
        token_store=token_store,
        lock_factory=lambda: RedisLock(redis, "lock:strava_token_refresh"),
    )


def build_gemini_client(settings: Settings, redis: UpstashRedis) -> GeminiClient:
    from google import genai

    runtime = genai.Client(api_key=settings.gemini_api_key)
    counter = DailyCallCounter(redis, settings.gemini_daily_call_cap)
    return GeminiClient(runtime, counter)


def build_worker_deps(settings: Settings) -> WorkerDeps:
    db = build_database(settings)
    redis = build_redis(settings)
    gemini = build_gemini_client(settings, redis)

    return WorkerDeps(
        owner_chat_id=settings.telegram_owner_chat_id,
        training_goal_default=settings.training_goal,
        strava=build_strava_client(settings, db, redis),
        telegram=TelegramClient(settings.telegram_bot_token),
        orchestrator=OrchestratorAgent(gemini, settings.gemini_model_orchestrator),
        strength_analyst=StrengthAnalystAgent(gemini, settings.gemini_model_analyst),
        endurance_analyst=EnduranceAnalystAgent(gemini, settings.gemini_model_analyst),
        chart_agent=ChartAgent(gemini, settings.gemini_model_orchestrator),
        conversation=ConversationAgent(gemini, settings.gemini_model_conversation),
        session_manager=SessionManager(redis, settings.session_ttl_seconds),
        profile_repo=ProfileRepo(db),
        exercise_history=ExerciseHistoryRepo(db),
        event_log=EventLog(db),
        # Orchestrator model: classification is a cheap, narrow task and this
        # runs at most once per never-seen-before exercise.
        muscle_resolver=MuscleMapResolver(
            LearnedMuscleMapRepo(db), gemini, settings.gemini_model_orchestrator
        ),
        # Backs the conversational data-fetch step (e.g. "how's this week looked?").
        db=db,
    )
