-- Learned exercise -> muscle-group mappings.
--
-- app/metrics/muscle_mapping.json is a static seed table and will always lag
-- behind a real exercise vocabulary: every new movement logged in Hevy that
-- isn't in it gets its volume silently excluded from muscle-group totals.
-- Rather than hand-curating that JSON forever, unknown exercises are resolved
-- once by the LLM and cached here, so each new movement costs exactly one
-- classification call and is permanent thereafter.
--
-- `source` distinguishes 'llm' (auto-resolved) from 'manual' (a human-entered
-- correction, currently only via direct SQL/repo call — no bot command yet), so
-- a correction is never overwritten by a later auto-resolve.
CREATE TABLE IF NOT EXISTS learned_exercise_muscles (
    exercise_key    TEXT PRIMARY KEY,           -- normalize_exercise_name() output
    display_name    TEXT NOT NULL,              -- as it appeared in the Strava description
    primary_groups  TEXT[] NOT NULL DEFAULT '{}',
    secondary_groups TEXT[] NOT NULL DEFAULT '{}',
    source          TEXT NOT NULL DEFAULT 'llm' CHECK (source IN ('llm', 'manual')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
