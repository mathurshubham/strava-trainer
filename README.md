# LiftMate

A personal Telegram bot that reacts to your completed workouts within a minute, sends an unprompted deep-dive analysis, then holds a 30-minute conversation about it. Weight training is parsed from the Strava activity description Hevy writes when it syncs; runs use Strava's streams/laps directly. All arithmetic (volume, e1RM, PRs, HR zones, decoupling, ACWR, ...) is computed in plain Python — the LLM (Gemini) only interprets already-computed numbers, never calculates them.

See [`LiftMate_PRD.md`](./LiftMate_PRD.md) for the full spec this was built against, and [`DECISIONS.md`](./DECISIONS.md) for every judgement call and live-docs finding made along the way — read that before assuming anything below is exactly what the PRD originally asked for. The single biggest deviation: **lifting data comes from Strava's activity description, not the Hevy API** (no Hevy Pro needed, but the parser is provisional — see DECISIONS.md).

## Architecture

```
Strava ──webhook──→ /api/webhook/strava ──┐
Telegram ──webhook─→ /api/webhook/telegram ┤── QStash (queue) ──→ /api/worker (the brain)
                                                                         │
                                              ┌──────────────────────────┼───────────────────┐
                                              ▼                          ▼                    ▼
                                    Orchestrator (LLM)          Data Agent (pure Python)   Memory Manager (Redis)
                                              │                          │
                              ┌───────────────┼────────────┬─────────────┘
                              ▼               ▼            ▼
                    Strength Analyst   Endurance Analyst   Chart Agent (picks; matplotlib renders)
                              └───────────────┴────────────┘
                                              ▼
                                   Telegram Renderer → sendMessage/sendPhoto
```

Postgres (Neon/Supabase) holds durable data — tokens (encrypted), activities, parsed sets, PRs, metrics. Redis (Upstash) holds ephemeral session/rate-limit state. QStash gives the webhook handlers their <200ms ack requirement: they validate, dedupe, enqueue, and return — the actual LLM work (5-40s) happens in `/api/worker`.

## Repository layout

```
api/index.py          The single Vercel entrypoint (WSGI app) — path-routes to app/web/*.py; see DECISIONS.md for why it's one file, not one per route
app/
  config.py           The ONLY place env vars are read (fails fast at import)
  clients/            Strava, Telegram, Gemini API clients
  agents/             orchestrator, data_agent (no LLM), strength/endurance analysts, chart_agent, conversation
  metrics/             strength.py, endurance.py — pure Python, zero LLM, fully unit tested
  charts/              theme + all 22 presets (matplotlib, Agg backend, in-memory PNG)
  parsers/             the provisional Hevy-Strava-description parser
  memory/              session state (30-min sliding window, pronoun resolution)
  render/telegram.py   markdown -> whitelisted HTML -> chunked, tag-balanced messages
  storage/             Postgres + Redis + QStash adapters
  prompts/*.md         every LLM prompt, versioned as plain files — never inline strings
  web/                 pure request-handling logic per endpoint (unit-testable without a server)
  platform/            Vercel HTTP adapter + dependency-injection wiring
migrations/           SQL schema
scripts/               one-time/maintenance CLIs (see below)
tests/                 165+ tests, pure-logic modules covered thoroughly
```

## Setup

### 1. Prerequisites

You'll need accounts/credentials from:
- **Telegram**: a bot token from [@BotFather](https://t.me/BotFather) (`/newbot`), plus your own numeric chat ID (message [@userinfobot](https://t.me/userinfobot) to get it)
- **Strava**: an API application at [strava.com/settings/api](https://www.strava.com/settings/api) (gives you a client ID + secret)
- **Google Gemini**: an API key from [Google AI Studio](https://aistudio.google.com/apikey) — use a billed project, not the free tier, if this will touch real health data (see Privacy note below)
- **Postgres**: a free instance from [Neon](https://neon.tech) or [Supabase](https://supabase.com)
- **Upstash**: a free [Redis](https://upstash.com) database and a [QStash](https://upstash.com/docs/qstash) instance
- **Vercel**: an account to deploy to (this is built as a Vercel Python app)

### 2. Fill in `.env`

```bash
cp .env.example .env
```

Fill in each value in `.env` — never commit this file or paste real values into chat/issues (it's already gitignored). Generate the two local-only secrets yourself:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # → ENCRYPTION_KEY
python -c "import secrets; print(secrets.token_hex(32))"                                     # → INTERNAL_API_SECRET
python -c "import secrets; print(secrets.token_hex(16))"                                      # → STRAVA_VERIFY_TOKEN / TELEGRAM_WEBHOOK_SECRET
```

`APP_URL` and `STRAVA_SUBSCRIPTION_ID` can be left blank for now — you'll fill those in after the first deploy (steps 4–5).

### 3. Install and test locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest                          # 165+ tests, all pure logic — no live credentials needed
```

### 4. Apply the database schema

```bash
python -c "
from pathlib import Path
from app.config import get_settings
from app.storage.db import Database
Database(get_settings().database_url).apply_migrations(Path('migrations'))
"
```

(Safe to re-run — every statement is `IF NOT EXISTS`.)

### 5. Deploy to Vercel

```bash
npm i -g vercel   # if you don't have it
vercel login
vercel            # first deploy, follow prompts
vercel env add    # add every key from .env to the Vercel project (Production + Preview)
vercel --prod
```

Also add a Vercel project env var named exactly `CRON_SECRET` with the **same value** as your `INTERNAL_API_SECRET` — Vercel Cron sends it automatically as `Authorization: Bearer $CRON_SECRET`, which `api/index.py`'s cron routes check against `INTERNAL_API_SECRET`.

Copy the deployment URL into `APP_URL` in `.env` (and as a Vercel env var), then redeploy so the running app knows its own URL.

### 6. Connect Strava (one-time)

```bash
python scripts/bootstrap_oauth.py     # prints an authorize URL — open it, log in, approve
python scripts/setup_strava_webhook.py create   # copy the returned id into STRAVA_SUBSCRIPTION_ID, redeploy
```

### 7. Connect Telegram (one-time)

```bash
python scripts/setup_telegram_webhook.py set
```

### 8. Optional: seed history so day-one PRs/comparisons aren't empty

```bash
python scripts/backfill_history.py --days 90
```

## Testing

```bash
pytest              # everything pure-logic: metrics, renderer, charts, parser, security, agents, webhook/worker dispatch
pytest -k strength   # just the strength engine, etc.
```

What's tested with real fixtures vs. what needs a live run once deployed:
- **Fully unit tested, no credentials needed**: all metrics math (including the Δt-weighted HR-zone test that catches the exact "counted samples instead of seconds" bug the PRD warns about), the Telegram HTML renderer/sanitizer/chunker, all 22 chart presets, the muscle-group mapping, session memory + pronoun resolution, config validation, Strava/Telegram/Gemini clients (mocked HTTP), and the full webhook → worker → briefing dispatch logic (fake Strava/Telegram, real Gemini structured-output plumbing against a fake model runner).
- **Verified live during original development** (see DECISIONS.md): Postgres migrations applied to a real Supabase instance, Redis round-trip, both Gemini models called for real, Telegram bot token validated read-only.
- **Needs a live deployment to fully verify**: the actual Strava webhook → QStash → worker → Telegram send round trip, and the OAuth consent flow (needs your own Strava login in a browser).
- **Needs a real Hevy→Strava description sample** (see DECISIONS.md): the weight-training parser is provisional until validated against real data.

## Privacy note

Gemini's free tier lets Google use your prompts to improve their products — for health/fitness data, a billed project (pennies/month at this call volume) avoids that. This is your call to make, not something the code decides for you (`STRAVA_LLM_ANALYSIS_ENABLED` and `GEMINI_DAILY_CALL_CAP` in `.env` are the relevant levers either way).

## Commands

`/start` `/help` `/last` `/week` `/pr` `/chart` `/compare` `/profile [goal <text>]` `/forget` `/debug` — see DECISIONS.md for which of these are full-featured vs. a simpler v1 stub.

## Wiping data

```bash
python scripts/delete_all_data.py    # requires typing DELETE to confirm — irreversible
```
