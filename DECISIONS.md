# DECISIONS.md

Every judgement call, live-docs discrepancy, and undocumented-behavior finding, with the date. Per PRD Appendix C.5: "Every time you hit an undocumented API behaviour, a changed limit, or a judgement call, write it down."

---

## 2026-07-26 — Credential rotation (PRD §0.1)

Developer confirmed the Strava client secret, Strava tokens, and Telegram bot token shared during planning were rotated before any code was written. New values went directly into `.env` (never pasted into chat).

## 2026-07-26 — Lifting data source: Strava description, not Hevy API

**This is the single biggest architectural deviation from the PRD**, made at the developer's explicit instruction rather than the PRD's own recommendation.

The PRD's default design (§2.1) uses the Hevy API as the source of truth for lifting — full structured set/rep/weight data, `exercise_templates` for muscle-group metadata, `exercise_history` for "what did I lift last time." It explicitly warns that parsing Strava's activity description is "fragile and lossy."

The developer chose to parse the Strava description instead, for two reasons: it avoids the Hevy Pro subscription requirement, and it collapses the entire Hevy↔Strava activity-matching problem (PRD §3.5) — Strava becomes the single trigger and single data source for both lifting and running. Consequences of this choice:

- **No Hevy client was built.** `app/clients/hevy.py` does not exist. `HEVY_API_KEY` is not in `.env`.
- **Muscle-group mapping is a static local table** (`app/metrics/muscle_mapping.json`), not sourced from Hevy's `exercise_templates`. It covers ~70 common exercises and needs manual additions as your exercise vocabulary grows — the parser logs a warning (not a silent drop) for anything unmapped.
- **"What did I lift last time" is answered from our own Postgres history** (`exercise_sets` table, populated every time a lifting activity is processed), not a Hevy endpoint.
- **The Strava-description parser (`app/parsers/hevy_strava_description.py`) is PROVISIONAL.** I could not obtain a real Hevy→Strava export sample during this build, so the parser is written against the commonly-observed format (exercise name on its own line, followed by `<weight><unit> x <reps>` lines, optionally `Set N:`-prefixed, optionally tagged `(warm up)`/`(failure)`/`(drop set)`). **This must be validated against 3-5 real activity descriptions before going live** — paste them into `tests/fixtures/` and re-run `tests/test_parser_hevy_strava_description.py` against the real shape. If the real format differs, only this one file needs to change; everything downstream (metrics, storage schema, agents) is unaffected because it consumes the parser's `RawParsedWorkout` output, not raw text.

## 2026-07-26 — Strava API policy (PRD §0.3): Option A, accept the risk

Developer chose **Option A** explicitly, and — because of the decision above — this now applies to *both* running and lifting data, not just running as the PRD assumed (since lifting data is now sourced from Strava's description field too, it's Strava Data under the policy same as an activity's streams). Rationale: personal, single-user, non-commercial account; data never leaves the developer's own Strava account. `STRAVA_LLM_ANALYSIS_ENABLED` defaults to `true` (flip to `false` in `.env` to fall back to deterministic-only if this position changes).

## 2026-07-26 — Hosting: Vercel, Python runtime confirmed

Verified against live Vercel docs (fetched 2026-07-26): the Python runtime supports either an ASGI/WSGI `app`/`application` object, **or** a plain `BaseHTTPRequestHandler` subclass named `handler` in any `.py` file under `api/` — each such file becomes its own Vercel Function. This build uses the latter (matches the PRD's one-file-per-endpoint repo layout without adding FastAPI/Flask as a dependency). Standard Python bundle limit applies uncompressed, with a larger limit on Fluid Compute (public beta) — exact current byte ceilings weren't stated numerically in the fetched doc; if `matplotlib` pushes the bundle over the limit, `vercel.json`'s `excludeFiles` is already configured to drop tests/fixtures, and Fluid Compute is the next lever.

## 2026-07-26 — Gemini models and daily-cap verified live

Live-called (not just looked up) both configured models with the real `GEMINI_API_KEY` from `.env`:
- `gemini-3.1-flash-lite` (orchestrator) — structured output round-trip confirmed working.
- `gemini-3.5-flash` (analyst/conversation) — structured output round-trip confirmed working.

Both remain GA as of 2026-07-26. Newer options exist (`gemini-3.6-flash`, `gemini-3.5-flash-lite`) if you want to switch later — just change the env var, `app/clients/gemini.py` never hardcodes a model ID.

## 2026-07-26 — Strava API base URL

Confirmed via the live Strava changelog: migration from `https://www.strava.com/api/v3` to `https://api-v3.strava.com` takes effect **4 January 2027**, not before. `STRAVA_API_BASE` is a config value (`app/config.py`) for exactly this reason — update it in `.env` when that date approaches.

## 2026-07-26 — QStash signature verification scheme

Verified via Upstash docs: `Upstash-Signature` is an HS256 JWT with claims `iss="Upstash"`, `sub=<endpoint URL>`, `exp`, `nbf`, and `body=<sha256 hex of the raw request body>`. `app/security/auth.py::verify_qstash_signature` implements this manually (via PyJWT) rather than the `qstash-py` SDK, verified against both the current and next signing key to survive key rotation. Live-tested against real signing keys from `.env` inside the worker dispatch tests (`tests/test_web_worker.py`), though not against an actual QStash-delivered request (needs a deployed `APP_URL` first).

> **CORRECTED 2026-07-26 — the `body` claim is base64, not hex.** See the entry below.

## 2026-07-26 — CORRECTION: QStash's `body` claim is base64, not hex — every real delivery 401'd

The entry above was written from the docs' prose and never checked against a real QStash-delivered request (explicitly noted as untested at the time). It was wrong, and the tests were wrong in exactly the same way — `tests/test_security.py` and `tests/test_web_worker.py` both *built* their fixture JWTs with `hexdigest()`, so 167 tests passed while the endpoint rejected 100% of real traffic. A self-consistent wrong assumption on both sides of the test.

Symptom: `POST /api/worker` returned 401 for every QStash delivery, so no Telegram command or Strava briefing was ever processed, while `POST /api/webhook/telegram` returned 200 and QStash's `/v2/publish` returned 201 — i.e. the failure was invisible from both ends.

Diagnosed by pulling a real delivered request out of ngrok's inspector API (`http://127.0.0.1:4040/api/requests/http`, which retains full headers and raw bodies) and decoding the actual JWT. Claims from a genuine delivery:

```
iss  = "Upstash"                                                        ✓ matched
sub  = "https://your-domain.ngrok-free.dev/api/worker"        ✓ matched
body = "v4gTZlAJpZHHhJreR412q79LU_IHxL3DLaKdnXtgYrE="                   ✗ compared against hex
nbf  = -62135596800   (Go's zero time — must not be treated as meaningful)
```

The HMAC signature itself verified fine against `QSTASH_CURRENT_SIGNING_KEY`, confirming the keys in `.env` were correct and the body claim was the only defect. That `body` value is the **base64 of the digest bytes**, URL-safe alphabet (`_` present), with `=` padding.

Fix in `verify_qstash_signature`: compare `base64.urlsafe_b64encode(sha256(body).digest())`, normalising both alphabets and stripping padding before a constant-time compare — Upstash's own SDKs compare unpadded, and the standard-vs-URL-safe alphabet choice isn't guaranteed, so all four spellings are accepted. Hex is now explicitly rejected by a named regression test.

Two lessons worth keeping: a hand-rolled crypto check whose test fixtures are built by the same assumption as the code under test proves nothing — the fixture must come from a real captured sample. And ngrok's request inspector is the tool that made this a 10-minute diagnosis; it retains raw bodies and headers, and can replay a request after a fix.

**Verified live**: real QStash-delivered `/api/worker` requests now return 200 and process the payload (confirmed with `/start` and `/help` telegram_update payloads round-tripping to `{"status": "done"}`). This closes the "QStash publish → worker → Telegram send round trip" item listed as untested in the entry above.

## 2026-07-26 — Training goal, units, briefing trigger, physiology

- Training goal: "Hybrid — lifting + running, general fitness", mutable anytime via `/profile goal <text>` — the bot acknowledges the change and folds it into analyst prompts from then on.
- Units: kg / km throughout.
- Briefing fires for every completed activity in v1 (no duration/distance threshold) — revisit `BRIEFING_MIN_DURATION_SECONDS`/`BRIEFING_MIN_DISTANCE_METERS` in `.env` if a short walk ever triggers an unwanted briefing.
- `MAX_HR=<redacted>`, `RESTING_HR=<redacted>`, `LTHR=<redacted>`, `BODYWEIGHT_KG=<redacted>` were supplied directly (not estimated) — zone math uses the LTHR-based table (PRD §4.2 priority order: custom Strava zones > LTHR/MAX_HR > age-estimate; LTHR is present so that's the active source).

## 2026-07-26 — Live infrastructure verified end-to-end (before deployment)

With real credentials in `.env`, the following were actually exercised against the live services (not mocked):
- **Postgres (Supabase)**: `migrations/001_init.sql` applied successfully; all 8 tables confirmed present; `ProfileRepo` read/write confirmed, seeded with the real physiology numbers above.
- **Redis (Upstash)**: SET/GET/DEL round-trip confirmed.
- **Gemini**: both configured models confirmed live, per above.
- **Telegram**: bot token confirmed valid via `getMe` (read-only — confirmed identity as `@LiftMateBot` without sending any message).
- **All six `api/*.py` entrypoints** import cleanly with real settings, confirming the full dependency-injection chain (`app/platform/bootstrap.py`) wires together without error. (Note: these six files were later consolidated into a single `api/index.py` — see the correction entry below — but the dependency-wiring verification still applies.)

**Not yet tested live** (each needs a step only the developer can do, or a deployed URL that doesn't exist yet):
- Strava webhook subscription creation (`scripts/setup_strava_webhook.py create`) — needs `APP_URL` pointing at a live deployment.
- Telegram webhook registration (`scripts/setup_telegram_webhook.py set`) — same dependency.
- An actual Telegram message being sent to the owner chat — deliberately not done from this session; sending a message is something Claude Code treats as requiring explicit per-action confirmation, so `getMe` (read-only) was used instead to validate the token.
- The QStash publish → worker → Telegram send round trip in production conditions.

## 2026-07-26 — Strava OAuth flow completed live, in the browser

Completed the actual authorization end-to-end (with the developer's explicit go-ahead each step): navigated to strava.com/settings/api, found the app's **Authorization Callback Domain** was pointed at an unrelated old Netlify project (`some-other-project.netlify.app` — a leftover from a different, earlier project), updated it to match the local ngrok tunnel domain, saved, then completed the OAuth consent screen. Verified afterward, live: tokens decrypt correctly from Postgres, and a real `GET /athlete` call using the stored access token returned athlete id `123456789`, matching `STRAVA_ATHLETE_ID` in `.env` exactly.

Also hit ngrok's free-tier browser-warning interstitial (`ERR_NGROK_6024`) on the redirect — that's expected for any browser-initiated visit to a free ngrok domain (API/curl clients don't see it, which is why earlier curl-based smoke tests didn't hit this). One click through "Visit Site" and the OAuth callback completed normally.

## 2026-07-26 — CORRECTION: Vercel's Python runtime does NOT support one-function-per-file or BaseHTTPRequestHandler, despite the docs

The initial build followed the live Vercel docs literally: one `BaseHTTPRequestHandler`-named `handler` class per file under `api/`. This turned out to be wrong in practice, discovered via three separate live failures while getting local dev running:

1. `vercel build` failed outright: `No python entrypoint found in default locations, but found potential entrypoints: [all six handler files]` — this CLI version (57.0.0) requires exactly **one** Python entrypoint, declared via `[tool.vercel] entrypoint = "module:variable"` in `pyproject.toml`. It does not deploy one function per `api/*.py` file the way the docs' wording implied.
2. Adding `pyproject.toml` silently changed how Vercel resolves Python dependencies: **it stopped reading `requirements.txt` entirely** and looked for `[project.dependencies]` in `pyproject.toml` instead (via `uv`). The first consolidated build "succeeded" but only installed `pip` and Vercel's own runtime shim — none of `httpx`/`pydantic`/`google-genai`/etc. — which would have crashed on first real request. Fixed by declaring the full dependency list under `[project.dependencies]` in `pyproject.toml` and running `uv lock`; `requirements.txt` is now a hand-kept mirror for local `pip install` convenience only.
3. Even after fixing (1) and (2), `vercel dev` failed per-request with `Could not determine the application interface for 'api.index:handler' — Expected either an ASGI app... or a WSGI app`. The runtime genuinely does not support a `BaseHTTPRequestHandler` class as the top-level entrypoint (only as one of several *candidate* shapes it apparently used to auto-detect per-file, per point 1 — but not as the single declared entrypoint). Fixed by rewriting `api/index.py` as a plain WSGI `app(environ, start_response)` callable and pointing `[tool.vercel] entrypoint` at `api.index:app`.

**Net result**: `api/` now has a single file, `api/index.py`, a WSGI app that path-routes to the same `app/web/*.py` handler functions that were already transport-agnostic (they take plain `dict`/`bytes` and return `WebResponse` — none of that logic or its tests needed to change). `app/platform/http_adapter.py` was rewritten from a `BaseHTTPRequestHandler` adapter to a WSGI-`environ` adapter.

**A fourth, unrelated local-only issue** surfaced during the same debugging session: `vercel dev` runs `uv pip install vercel-runtime==0.16.0` (which requires Python ≥3.12) from the project directory, and `uv` was picking up this repo's own `.venv` (created with the system's Python 3.11.9 for local `pytest` runs) instead of the separate Python 3.12 environment `vercel build` sets up under `.vercel/python/`. Fixed by recreating the local dev `.venv` with Python 3.12 (`/opt/homebrew/bin/python3.12 -m venv .venv`) and bumping `requires-python` to `>=3.12` — this was never a code bug, just a version mismatch between the ad-hoc local venv and what Vercel's tooling expects to find in the same directory.

**Verified working after all four fixes**: `GET /api/webhook/strava` (validation echo), `GET /api/oauth/strava/callback` (missing-param 400), `POST /api/worker` (bad-signature 401) all returned correct status codes through `vercel dev` on `localhost:3000` *and* through the real ngrok tunnel — confirmed live, not just by reading code.

## 2026-07-26 — Strava data pull verified live; parser validated against real exports

Pulled the athlete's real data end to end (read-only probe, no writes): `GET /athlete` → id `123456789`, `GET /athlete/activities` over a 90-day window → **37 activities** (28 WeightTraining, 7 Run, 2 Walk), then `GET /activities/{id}` for full descriptions. Rate-limit headers parsed correctly (3/200 fifteen-minute, 5/2000 daily). **The Strava pull was never broken** — the bot appeared unable to see workouts because of the QStash 401 (above) plus the two parser/mapping defects below.

Four real Hevy→Strava descriptions are now captured verbatim in `tests/fixtures/hevy_real_*.txt`, closing the "provisional parser" risk flagged in the 2026-07-26 lifting-data-source entry. The real format is `Set N: <weight> kg x <reps>` — note the space before `kg` and a trailing space on every line — under a bare exercise name, with a `Logged with hevyapp.com` footer. Two defects only visible against real data:

1. **The Hevy footer was parsed as an exercise.** `_BOILERPLATE_RE` matched `hevy\.com`, but the real footer says `hevyapp.com` — which does not contain `hevy.com`. Every workout gained a phantom "Logged with hevyapp.com" exercise. Now matches `\bhevy` anywhere.
2. **Zero-set exercises were emitted rather than dropped.** A name with no parseable set lines under it is far more likely to be an unclassified header than a real exercise, and it reached muscle-mapping and history as a phantom. Now warned and dropped. (`tests/test_parser_hevy_strava_description.py::test_warns_on_exercise_with_no_parseable_sets` asserted the old behaviour and was updated deliberately.)

Verified after the fix on a real session: 4,840 kg over 11 working sets, muscle-group volumes across traps/shoulders/forearms/triceps, push/pull 1.18, zero parser warnings.

## 2026-07-26 — Unknown exercises are LLM-resolved and cached, not hand-added to the JSON table

Running the four real workouts through `muscle_groups_for` left **6 of 14 distinct exercises unmapped** — their volume silently excluded from every muscle-group total, push/pull ratio, and balance chart. Two causes: the seed table uses singular `tricep_*` while Hevy writes "Triceps", and real names carry qualifiers ("Single Arm", "Rope", "Seated", "Iso-Lateral") that never match exactly.

I first hand-added the missing entries to `muscle_mapping.json`. The developer rejected that approach — *"you will keep losing track, make it as it comes"* — and they're right: hand-curation guarantees the table drifts out of date the moment a new movement is logged, and the failure is silent. Reverted, and replaced with runtime resolution.

`app/metrics/muscle_resolver.py::MuscleMapResolver` is now a three-tier lookup behind the same `(name) -> ({group: weight}, found)` callable signature as the static function:

1. **static JSON** — no I/O, covers the common vocabulary (unchanged, still the seed)
2. **learned Postgres row** (`learned_exercise_muscles`, migration `002`) — one query, covers everything seen before
3. **LLM classification** — one call, ever, per never-seen exercise; result persisted to tier 2

Design points worth keeping:
- The metrics engine stays pure. `compute_muscle_group_volumes(exercises, resolver=None)` defaults to the static table, so `app/metrics/strength.py` remains DB-free and unit-testable; production injects the resolver via `WorkerDeps.muscle_resolver`.
- **The model's output is not trusted.** Groups outside `ALLOWED_GROUPS` are dropped (an invented group name would silently skew push/pull and upper/lower ratios), and a confidence below 0.4 is refused.
- **Every failure degrades to the old behaviour** — no resolver wired, daily cap hit, malformed output, or Postgres down all yield `found=False` and a parser warning, never an invented number.
- `source` distinguishes `'llm'` from `'manual'`; the upsert refuses to let an auto-resolve overwrite a human correction.
- Also added generic synonym/qualifier normalisation (`triceps`→`tricep`, strip `single_arm`/`rope`/`seated`/…) so known movements spelled a new way hit tier 1 without an LLM call. Deliberately excludes qualifiers that *do* change the muscle split — `incline`, `decline`, `close_grip`, `reverse`, `sumo`.

Verified live: all 14 distinct real exercises resolve, 5 were classified by Gemini and persisted, and a second pass with `gemini=None` resolved 14/14 from cache — confirming each new exercise costs exactly one call, ever.

**Follow-up worth doing:** there's no bot command to correct a wrong classification yet — `source='manual'` is only reachable via a direct repo call. A `/muscles` command to list and override learned mappings is the natural v1.1 addition.

## 2026-07-26 — Strava subscription existed but `.env` was blank; unconfigured now logs loudly

`scripts/setup_strava_webhook.py view` shows the subscription **was already created** — id `987654`, callback `https://your-domain.ngrok-free.dev/api/webhook/strava`, created `2026-07-26T12:12:49Z` (matching the `hub.challenge=57eabfab8c4e0ea3` validation GET in the dev log at 17:42:48 local). The returned id was simply never copied into `.env`.

The consequence was worse than "not wired up yet". `api/index.py` passes `settings.strava_subscription_id or -1`, so with the value blank every genuine event failed `verify_strava_webhook_identity` and returned `200 {"status": "ignored_not_ours"}` — a success code, so Strava never retried, and nothing was logged. Structurally the same invisible failure as the QStash 401: the endpoint answered "fine" while discarding 100% of real traffic.

Fixed by setting `STRAVA_SUBSCRIPTION_ID=987654`, and by making the misconfiguration loud: `app/web/strava_webhook.py` now logs an ERROR naming the env var when `expected_subscription_id < 0` (the unconfigured sentinel), while a genuinely foreign subscription stays at INFO. Both paths still return 200 — that part is correct and deliberate (PRD §3.1: a non-200 costs the event permanently). Two regression tests cover the split.

Worth noting as a pattern: three separate bugs this session (QStash 401, this, and the unmapped-exercise volume drop) all failed *silently and successfully*. On this architecture — webhooks that must return 200, at-least-once queues, best-effort enrichment — "returns 200" proves nothing. Verify by asserting on the effect, not the status code.

## 2026-07-26 — CORRECTION: the orchestrator had never actually run, ever — `dict[str,str]` response-schema fields are rejected by Gemini's Developer API

Testing "what's my bench PR?" and "how's this week looked?" live in Telegram, the bot replied with a generic "I don't have that loaded" both times, even after fixing the `fetched_data_json="{}"` hardcoding below. Checked the `vercel dev` log for the exchange: only one Gemini call appeared (`gemini-3.5-flash`, the conversation model) — **no call to `gemini-3.1-flash-lite`, the orchestrator model, ever happened.**

Called the orchestrator directly, outside its own try/except, to see the real exception:

```
ValueError: additionalProperties is only supported in Gemini Enterprise Agent
Platform mode, , not in Gemini Developer API mode.
```

`OrchestratorOutput.resolved_pronouns: dict[str, str]` (and `ChartSelection.params: dict[str, str]`) compile to a JSON Schema needing `additionalProperties` — an arbitrary-key map — which Gemini's Developer API structured-output mode (the mode this project's plain API key actually uses, per the Enterprise-vs-Developer distinction in that error) does not support. `GeminiClient.generate_structured` raises this `ValueError` from `_invoke`, **outside** its own retry `try/except` (which only wraps `_parse`), so it propagates straight out. `OrchestratorAgent.route()`'s `except Exception` then caught it and returned the safe fallback (`intent=UNCLEAR, target_entity=None, needs_data=["none"], route_to="conversation"`) — silently, every single time, since this code was first written.

This means **the orchestrator has never once successfully classified a user message in this project's history.** Every conversational reply the bot has ever sent was generated by the conversation agent working from `session_context` and `recent_turns` alone, with no real routing, no resolved pronouns, and (compounding the bug below) no fetched data. The 2026-07-26 entry claiming "structured output round-trip confirmed working" for both models tested a schema without a dict field, so it never exercised this path.

Fixed at the schema level, not by catching more exceptions (that would just make the fallback quieter, not make classification work): `resolved_pronouns` is now `list[PronounResolution]` (`{pronoun, resolved_to}` objects) and `ChartSelection.params` is now `list[ChartParam]` (`{key, value}` objects) — both render as array-of-objects schemas, which Gemini's controlled generation handles fine. Updated the two prompt files' field descriptions to match, and the one consumption site in `worker.py` (`route.resolved_pronouns.items()` → iterate the list).

**Verified live against the real Gemini API**, not just re-passing tests: the orchestrator now correctly returns `target_entity=TargetEntity(type='exercise', ref='bench press')` for "what's my bench PR?", correctly resolves "it" to a workout ref given session context, and the chart agent now correctly picks `s03_e1rm_trend` with proper params for "chart my bench progress" — none of which had ever worked before. Confirmed end-to-end in the real Telegram app: "what's my bench PR?" now returns "Your heaviest bench press on record is 75.0kg... best estimated 1RM is 87.5kg", and "how's this week looked?" returns real aggregated numbers from Postgres.

**Not touched, flagged for later**: `route.route_to` and `route.needs_chart` are computed correctly now but still never consumed in `worker.py` — a "chart my bench progress" request gets a text-only reply; `chart_agent.select()` is wired as a dependency but never called from the conversation path. This is a real gap, but distinct from what broke tonight and out of scope for this fix.

## 2026-07-26 — CORRECTION: `_handle_conversation_turn` never fetched real data — `fetched_data_json` was hardcoded to `"{}"`

Same live-testing session as above, found first (before the deeper orchestrator bug). `app/web/worker.py::_handle_conversation_turn` called `deps.conversation.reply(..., fetched_data_json="{}", raw_activity_text="", ...)` unconditionally — the orchestrator's `needs_data`/`target_entity` verdict was computed and then thrown away. The conversation prompt's own rule 2 ("if you don't have the data needed to answer, say so... rather than guessing") means the model was behaving *correctly* given an always-empty context; the bug was entirely in never populating that context.

Added `_fetch_data_for_turn(route, deps)` in `worker.py`: for `target_entity.type == "exercise"`, queries `ExerciseHistoryRepo.get_exercise_bests`/`get_previous_occurrence` for real PRs and last-time data; for `type == "period"`, calls the existing `app/web/cron.py::compute_weekly_stats` (already built for the weekly digest cron, now reused on-demand — matches the "small follow-up, not a redesign" note in the Command coverage entry below). `target_entity.type in ("workout", "activity")` is deliberately left unfetched — a follow-up about the workout just briefed is answerable from `recent_turns` alone (it's literally that briefing's text), so re-fetching there would be redundant DB/API load, not a fix.

Added `db: Database | None` to `WorkerDeps` (wired in `bootstrap.py`) so the period-query path has something to call `compute_weekly_stats` with; `None` by default so existing tests constructing `WorkerDeps` without a live Postgres connection are unaffected.

This fix was necessary but, on its own, insufficient — the orchestrator bug above meant `route.target_entity` was always `None` regardless, so `_fetch_data_for_turn` always returned `{}` until both fixes landed together.

## 2026-07-26 — Backfill run against real data; parser correctly dropped athlete's personal notes

Ran `scripts/backfill_history.py` for real (not a dry run) against the 90-day window confirmed earlier (28 WeightTraining activities). All 28 recorded successfully with plausible volumes (3,265kg–8,726kg per session). One activity's description contained the athlete's own inline notes rather than exercise names — "Long workdays", "Content creation", "New Fatherhood", "Trying to squeeze every ounce" — and the parser correctly emitted warnings and dropped all four rather than recording them as zero-set phantom exercises, validating the fix made earlier this session (see the parser-fixes entry above). Took ~20 minutes wall-clock for 28 activities (Strava API + DB round-trips, no code bug — CPU time was ~5s of the ~20min elapsed, i.e. this is normal network latency, not a hang).

## 2026-07-26 — Local gitleaks installation quirk (not a project defect)

While verifying the Phase 0 exit criterion "gitleaks blocks a planted fake secret," the Homebrew-installed `gitleaks 8.30.1` on this machine returned "no leaks found" even for a canonical `-----BEGIN RSA PRIVATE KEY-----` block with the `private-key` rule explicitly force-enabled — meaning its embedded default ruleset isn't functioning in this specific install. This looks like a broken/incomplete bottle rather than a `.pre-commit-config.yaml` misconfiguration (the config matches gitleaks' own documented pre-commit setup). Re-verify with `pre-commit run gitleaks --all-files` once pre-commit is actually installed into a real git repo for this project — that path pulls its own gitleaks build via the hook's `repo:`/`rev:` pin and may not share this issue.

## 2026-07-26 — Command coverage simplifications

Per the developer's request for `/tag`-style commands and a mutable goal, `/profile goal <text>` was added beyond the PRD's literal `/profile` spec (view/set HRmax, LTHR, bodyweight, units, goals — goal-setting via a sub-argument is the new part). Given the scope of a full multi-step guided flow, these commands are intentionally simpler than the PRD's ideal for v1:
- `/compare` and `/chart` don't have a dedicated inline-keyboard picker yet — they point the user at natural-language phrasing ("chart my bench progress", "compare it to Tuesday"), which the orchestrator + chart agent already handle.
- `/week` and `/pr` give a basic pointer rather than a fully formatted report; `app/web/cron.py::compute_weekly_stats` has the real aggregation query and is wired into the weekly cron digest — extending `/week` to call the same function on demand is a small follow-up, not a redesign.
- `/debug` doesn't yet surface live queue depth (QStash has no simple REST "depth" endpoint) — it points at the Vercel/QStash consoles instead.

## 2026-07-26 — Two more real defects found by pulling this week's data straight from Strava, bypassing the app

Asked "what did I actually log this week" and compared Strava's API directly against Postgres (`scripts/pull_week_isolated.py`, new debug script — no DB/LLM in the path). Strava had 4 activities in the trailing 7 days (3 lifts + one 7.22km run); Postgres's `activities` table had only 3, all lifts. Root cause: `ExerciseHistoryRepo.record_strength_session` (the only writer to `activities`) is only ever called from `_run_strength_briefing`; `_run_endurance_briefing` builds the context, sends the Telegram briefing, and then throws the activity away — it never persists anything. Every run/ride has always been briefed once and then permanently missing from `compute_weekly_stats`, `/last`, and `get_most_recent_activity_id`. Fixed by adding `record_endurance_session` (mirrors the strength write path, plus a `distance_m` metric row) and calling it from `_run_endurance_briefing` right after the context is built.

Second, separate defect while in the area: `compute_volume_kg` summed `weight_kg * reps` with no regard for equipment. Hevy (like every lifting tracker) logs the weight of a *single* dumbbell for two-handed dumbbell lifts — the four real fixtures in `tests/fixtures/hevy_real_*.txt` are full of this, e.g. `Shoulder Press (Dumbbell): 40 kg x 8` means two 40kg dumbbells, 80kg actually moved per rep, not 40kg. Every bilateral dumbbell exercise (all of them in the real fixtures except the explicitly single-arm one) had its volume — and everything downstream of it: muscle-group volume, push/pull ratio, session PRs — understated by roughly half. Fixed with `is_bilateral_dumbbell(exercise_name)` (matches "dumbbell" in the name, excludes single-arm/single-leg/unilateral variants) threaded through `compute_volume_kg`/`compute_comparison`/`build_exercise_performance` as a multiplier, applied only to volume/tonnage — `top_set_weight_kg` and `e1rm` deliberately stay per-hand, matching how lifters actually talk about dumbbell PRs ("I hit 30s for 8"), which is not what was reported broken.

Both fixes required wiping and reloading all historical data, since every previously recorded strength session's volume (and every run's total absence) was wrong at the row level, not just in a downstream read — `scripts/delete_all_data.py` followed by `scripts/backfill_history.py`, which was also extended in the same pass to backfill endurance activities (previously WeightTraining-only, for the same historical reason the live path never recorded them).

**A third defect surfaced by that reload, in the same area**: `compute_weekly_stats`'s volume query filtered `metrics.computed_at >= since` — but `computed_at` is a DB-default `now()` set at INSERT time, not the workout's actual date. Live operation mostly hides this (a briefing is normally inserted right when the activity happens, so the two are almost always the same moment), but the reload above inserted 28 historical sessions' `total_volume_kg` metric rows all at once *today* — so every one of them counted as "this week," reporting 250,055kg instead of the real ~26,613kg for the week's 2 actual lifting sessions. Fixed by joining to `activities.start_date` instead (matching how the distance query in the same function already does it). This bug would resurface every time `backfill_history.py` (or any future reprocessing job) runs, so it wasn't a one-off data-fixup — it was a real code defect, just one that only a bulk reload could expose. Re-verified against the reloaded data after the fix: `{total_volume_kg: 26612.5, total_distance_km: 7.2155, session_count: 4}`, matching Strava exactly for the trailing 7 days.

Full reload also hit one transient, non-code failure worth noting for anyone re-running this: `scripts/backfill_history.py --days 90` died partway through the endurance batch on `psycopg.OperationalError: ... No route to host` (a momentary Supabase network blip) — 28/28 lifts and 7/9 endurance activities had already landed by then. Since `record_strength_session`/`record_endurance_session` only dedupe the `activities` row itself (`ON CONFLICT`) and not `exercise_sets`/`personal_records`/`metrics`, blindly re-running the full command would have duplicated those child rows for the 35 already-successful activities. Diffed Strava's full activity list against what was actually in Postgres to find the exact 2 missing (`Night Run` 2026-07-15, `Solidarity Run` 2026-07-22) and loaded only those two directly via `backfill_history._load_endurance`, rather than re-running the whole batch.

## 2026-07-30 — Charts now render inside a conversation turn (v1 wiring)

`_handle_conversation_turn` (`app/web/worker.py`) previously ignored `route.needs_chart`/`route_to == "chart"` and always sent text — a message like "chart my bench progress" or the 📊 button never produced a photo. Wired it up: on a chart-needing turn (including the button, which arrives as the synthetic text `"[chart]"`) it calls `ChartAgent.select(...)`, then a new dispatcher `app/agents/chart_dispatch.py::render_selected_chart` fetches the data and renders a PNG, sent via the existing `send_photo` path with the sanitized LLM caption.

v1 deliberately wires only **four** chart ids whose data comes from existing tables with small SQL — `s03_e1rm_trend`, `s02_volume_trend`, `s08_pr_timeline`, `s12_frequency_heatmap` (new `ExerciseHistoryRepo` methods `get_e1rm_series`/`get_volume_series`/`get_pr_timeline`/`get_session_dates`; e1RM series reuses the pure `best_e1rm` helper, so no arithmetic in SQL). Every other id, and any chart whose history is too thin (< 2 trend points), returns `None` and the turn falls back to a plain text reply — never crashes, never sends an empty image. `available_data_keys_json` now carries the exact wired chart ids (filtered by whether an exercise is the subject) rather than abstract keys, so the agent picks a renderable id directly. Subject resolution uses `target_entity` → chart param → session focus, with a strict `focus.type == "exercise"` guard (a fresh post-briefing focus is a workout/activity id, so the button then defaults to the whole-session `s02_volume_trend`, which is correct). Note `s02_volume_trend` is lifting-only (endurance sessions write no `total_volume_kg` metric).
