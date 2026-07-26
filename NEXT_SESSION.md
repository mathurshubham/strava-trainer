# Where things stand — pick up here

Snapshot as of 2026-07-26. This is the tactical "what to do next" doc — for the full build history and every judgment call, see [`DECISIONS.md`](./DECISIONS.md); for general setup reference, see [`README.md`](./README.md). This file is safe to delete once you're fully deployed and don't need the handoff anymore.

## Status: local dev environment is live, OAuth-connected, backfilled, and the conversation path actually works now

- **197 tests passing** (`pytest -q`).
- **Real credentials are in `.env`** (Telegram, Strava, Gemini, Supabase Postgres, Upstash Redis, QStash) — never paste them into chat again, edit the file directly.
- **Postgres schema is applied** to your real Supabase instance (all 9 tables — migration `002` added `learned_exercise_muscles`), seeded with your physio profile (`MAX_HR=<redacted>`, `RESTING_HR=<redacted>`, `LTHR=<redacted>`, `BODYWEIGHT_KG=<redacted>`).
- **Strava OAuth is connected**, subscription `987654` exists and `STRAVA_SUBSCRIPTION_ID` is set — do NOT re-run `setup_strava_webhook.py create`, it will fail with "already exists".
- **History is backfilled**: `scripts/backfill_history.py` ran for real — 28 weight-training sessions recorded from the last 90 days.
- **The conversation path was completely dead until tonight** and is now fixed and verified live — see `DECISIONS.md`'s two 2026-07-26 CORRECTION entries. In short: the orchestrator's response schema used `dict[str,str]` fields, which Gemini's Developer API structured-output mode rejects outright — so the orchestrator has *never once* successfully classified a message in this project's history, always silently falling back to "no data needed." Fixed by changing those fields to list-of-objects schemas. Separately, `_handle_conversation_turn` was hardcoding an empty `fetched_data_json="{}"` regardless of what the orchestrator asked for — fixed by actually querying Postgres for exercise PRs and weekly stats. Both were required together; confirmed live in Telegram: "what's my bench PR?" and "how's this week looked?" now return real numbers.
- **Local dev server**: `vercel dev` on port 3000, tunneled through ngrok at `https://your-domain.ngrok-free.dev` (static domain — see "If ngrok's domain changes" below if it ever isn't).
- **Strava's Authorization Callback Domain** is set to `your-domain.ngrok-free.dev`.

## Known-fragile: restarting `vercel dev`

Twice this session, `vercel dev` came back up "Ready!" but every request 500'd with `FUNCTION_INVOCATION_FAILED` and nothing reached the app's own logger. Both times the cause was `.vercel/python/.venv` — Vercel's own separately-managed Python environment for local dev, NOT this repo's `.venv` — ending up with the wrong-platform `pydantic_core` wheel (Linux x86-64 instead of this Mac's arm64). Fix: `rm -rf .vercel/python` (confirmed gitignored/disposable) and restart `vercel dev`; it rebuilds automatically with the correct wheels. If a restart ever comes back 500 on everything, try this before anything else.

## What's NOT done yet — the immediate next steps, in order

```bash
cd /path/to/strava-trainer
source .venv/bin/activate

# 1. Make sure vercel dev + ngrok are both still running (separate terminals)
lsof -i :3000                          # should show a node process
curl -s http://127.0.0.1:4040/api/tunnels | grep public_url

# 2. Verify both webhook registrations are still healthy
python scripts/setup_strava_webhook.py view
python scripts/setup_telegram_webhook.py info

# 3. End-to-end test: log a real workout (Hevy sync -> Strava, or any Strava
#    activity) and watch for the Telegram briefing within ~60-90s. Check the
#    `vercel dev` terminal for tracebacks if nothing arrives.
```

## After local end-to-end works: deploy to Vercel for real

Local `vercel dev` + ngrok is fine for testing but not for daily use (ngrok tunnels aren't meant to run forever, and this whole setup depends on your laptop staying on). Once step 6 above works:

```bash
vercel --prod
```
Then in the Vercel dashboard (Settings → Environment Variables), bulk-paste your `.env` contents, plus add `CRON_SECRET` = same value as `INTERNAL_API_SECRET`. Set `APP_URL` to the real `*.vercel.app` URL, redeploy, then **repeat steps 2-4 above pointed at the real URL instead of ngrok** (including updating Strava's Authorization Callback Domain to the Vercel domain instead of the ngrok one).

## Known gotchas already solved (don't re-debug these — see DECISIONS.md for full detail)

- Vercel's Python runtime needed **one consolidated WSGI entrypoint** (`api/index.py`), not one file per route and not a `BaseHTTPRequestHandler` class, despite what the public docs show. Already fixed.
- `pyproject.toml` existing at all made Vercel stop reading `requirements.txt` — real dependencies now live in `pyproject.toml`'s `[project.dependencies]`, `requirements.txt` is a hand-kept mirror for local `pip install` only. Already fixed.
- Local `.venv` must be **Python 3.12** (matches what `vercel dev`'s own tooling expects) — already recreated. If you ever `rm -rf .venv` and rebuild it, use `python3.12 -m venv .venv`, not whatever `python3` defaults to.
- Strava's OAuth **Authorization Callback Domain** must exactly match whatever domain is in `APP_URL` (ngrok or Vercel) — already set correctly for the current ngrok domain.

## If ngrok's domain changes

If you restart ngrok without a reserved domain and get a new `*.ngrok-free.app`/`.dev` URL:
1. Update `APP_URL` in `.env` to the new domain.
2. Update strava.com/settings/api → My API Application → Edit → Authorization Callback Domain to the new domain.
3. Re-run `python scripts/bootstrap_oauth.py` (old tokens still work — you only need this if the redirect_uri itself changed and you're re-testing the OAuth flow, not for normal operation).
4. Re-run `python scripts/setup_strava_webhook.py create` and `python scripts/setup_telegram_webhook.py set` against the new domain.

## No longer provisional — parser validated, muscle mapping now self-maintaining

The Strava-description parser has been validated against four real Hevy exports (now in `tests/fixtures/hevy_real_*.txt`) and two defects were fixed — see `DECISIONS.md`. Unknown exercises no longer need hand-adding to `muscle_mapping.json`: they're classified by the LLM once and cached in Postgres (`learned_exercise_muscles`, migration `002`, already applied to your Supabase instance).

Two things to know about that:
- If a classification comes out wrong, there's no bot command to fix it yet. Override it directly: `LearnedMuscleMapRepo(db).save(key, name, primary, secondary, source="manual")` — `source="manual"` is protected from being overwritten by later auto-resolves.
- Check what it has learned with `SELECT display_name, primary_groups, secondary_groups FROM learned_exercise_muscles;`

## Still a real gap: charts never actually render inside a conversation

The orchestrator now correctly sets `needs_chart`/`route_to="chart"` and `ChartAgent.select()` works (verified live), but `app/web/worker.py::_handle_conversation_turn` never checks either field or calls `deps.chart_agent` — a message like "chart my bench progress" gets a text-only reply, no photo. Wiring this up (call `chart_agent.select()` when `route.needs_chart`, render via the matching `app/charts/*` function, `send_photo`) is the natural next follow-up in the same vein as tonight's fixes.
