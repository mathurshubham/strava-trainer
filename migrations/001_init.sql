-- LiftMate schema. Single-tenant (PRD §1.2) but every table is still keyed
-- properly rather than assuming "row 0" magic, so this doesn't have to be
-- rewritten if that assumption ever changes.
--
-- Run with: psql "$DATABASE_URL" -f migrations/001_init.sql
-- (or scripts/apply_migrations.py, which just runs every migrations/*.sql in order)

CREATE TABLE IF NOT EXISTS oauth_tokens (
    provider            TEXT PRIMARY KEY,
    access_token_enc    TEXT NOT NULL,
    refresh_token_enc   TEXT NOT NULL,
    expires_at          BIGINT NOT NULL,
    scope               TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per Strava activity we've fetched. For lifting activities, the
-- parsed Strava-description sets live in exercise_sets, keyed by strava_id.
CREATE TABLE IF NOT EXISTS activities (
    strava_id       BIGINT PRIMARY KEY,
    sport_type      TEXT NOT NULL,
    title           TEXT NOT NULL,
    start_date      TIMESTAMPTZ NOT NULL,
    duration_s      INTEGER NOT NULL,
    raw_json        JSONB NOT NULL,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ  -- set to fetched_at + STRAVA_CACHE_TTL_DAYS; purged by scripts/cron
);

CREATE INDEX IF NOT EXISTS idx_activities_sport_start ON activities (sport_type, start_date DESC);

-- Parsed working sets for a weight-training activity. This is our own
-- history store — since lifting data comes from the Strava description
-- (not the Hevy API), "what did I lift last time" comparisons are answered
-- from this table, not a Hevy endpoint.
CREATE TABLE IF NOT EXISTS exercise_sets (
    id                      BIGSERIAL PRIMARY KEY,
    strava_id               BIGINT NOT NULL REFERENCES activities (strava_id) ON DELETE CASCADE,
    exercise_name           TEXT NOT NULL,
    exercise_template_key   TEXT NOT NULL,  -- app.metrics.muscle_mapping.normalize_exercise_name(exercise_name)
    set_index               INTEGER NOT NULL,
    set_type                TEXT NOT NULL,  -- normal | warmup | failure | dropset
    weight_kg               DOUBLE PRECISION NOT NULL,
    reps                    INTEGER NOT NULL,
    rpe                     DOUBLE PRECISION,
    rest_seconds            INTEGER
);

CREATE INDEX IF NOT EXISTS idx_exercise_sets_template_key ON exercise_sets (exercise_template_key, strava_id);

CREATE TABLE IF NOT EXISTS metrics (
    id              BIGSERIAL PRIMARY KEY,
    source_id        BIGINT NOT NULL,
    source_type      TEXT NOT NULL,  -- 'activity' | 'exercise'
    metric_key       TEXT NOT NULL,
    metric_value     DOUBLE PRECISION NOT NULL,
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_metrics_source ON metrics (source_type, source_id, metric_key);

CREATE TABLE IF NOT EXISTS personal_records (
    id                      BIGSERIAL PRIMARY KEY,
    exercise_template_key   TEXT NOT NULL,
    record_type             TEXT NOT NULL,
    value                    DOUBLE PRECISION NOT NULL,
    achieved_at              TIMESTAMPTZ NOT NULL,
    source_id                BIGINT NOT NULL REFERENCES activities (strava_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_prs_template_key ON personal_records (exercise_template_key, record_type);

CREATE TABLE IF NOT EXISTS briefings (
    id              BIGSERIAL PRIMARY KEY,
    source_id       BIGINT NOT NULL REFERENCES activities (strava_id) ON DELETE CASCADE,
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    message_ids     JSONB NOT NULL DEFAULT '[]',
    analysis_text   TEXT
);

-- QStash is at-least-once delivery; the worker WILL get duplicates. This is
-- the idempotency guard (PRD §3.6).
CREATE TABLE IF NOT EXISTS event_log (
    id              BIGSERIAL PRIMARY KEY,
    provider        TEXT NOT NULL,
    event_hash      TEXT NOT NULL UNIQUE,
    payload         JSONB NOT NULL,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'received'  -- received | processing | done | failed
);

-- Single-row athlete profile — mutable via /profile, no user table needed
-- (PRD §1.2: single-tenant, this removes an enormous amount of complexity).
CREATE TABLE IF NOT EXISTS athlete_profile (
    id                  SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- enforce exactly one row
    max_hr              INTEGER,
    resting_hr          INTEGER,
    lthr                INTEGER,
    bodyweight_kg       DOUBLE PRECISION,
    weight_unit         TEXT NOT NULL DEFAULT 'kg',
    distance_unit       TEXT NOT NULL DEFAULT 'km',
    training_goal       TEXT NOT NULL DEFAULT 'Hybrid — lifting + running, general fitness',
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO athlete_profile (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
