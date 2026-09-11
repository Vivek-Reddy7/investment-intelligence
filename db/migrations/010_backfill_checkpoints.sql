-- 010 · Backfill checkpoints
--
-- Phase 7's defining constraint: a backfill over ~500 companies and ten years
-- of filings runs for hours under a provider rate limit, and it WILL be
-- interrupted -- a dropped connection, a rate-limit ban, GitHub Actions
-- hitting its job ceiling, a laptop closing. Restarting from zero is not an
-- acceptable answer, because each restart costs another several hours of
-- somebody else's rate limit.
--
-- So progress is checkpointed per unit of work. A resumed run skips what is
-- already DONE and retries what FAILED.
--
-- This is operational state, not a fact about the world, so it is mutable
-- (see the exceptions note in 005_append_only.sql).

CREATE TABLE backfill_checkpoints (
    job             text        NOT NULL,   -- e.g. 'fundamentals-2016-2026'
    instrument_id   bigint      NOT NULL REFERENCES instruments (instrument_id),

    state           text        NOT NULL DEFAULT 'PENDING'
        CHECK (state IN ('PENDING', 'IN_PROGRESS', 'DONE', 'FAILED', 'SKIPPED')),

    attempts        integer     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    facts_written   integer     NOT NULL DEFAULT 0 CHECK (facts_written >= 0),
    facts_rejected  integer     NOT NULL DEFAULT 0 CHECK (facts_rejected >= 0),

    last_error      text,
    updated_at      timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (job, instrument_id),

    CONSTRAINT failures_are_explained
        CHECK (state <> 'FAILED' OR last_error IS NOT NULL)
);

-- Drives "what is left to do", which is the only query a resume needs.
CREATE INDEX idx_checkpoints_pending
    ON backfill_checkpoints (job, state)
    WHERE state IN ('PENDING', 'IN_PROGRESS', 'FAILED');

COMMENT ON TABLE backfill_checkpoints IS
    'Per-instrument backfill progress. Operational state, deliberately '
    'mutable. IN_PROGRESS left behind by a crash is retried, not trusted: a '
    'crash mid-instrument may have written some facts, which is safe because '
    'fact writes are idempotent on (identity, value).';

COMMENT ON COLUMN backfill_checkpoints.state IS
    'SKIPPED means deliberately excluded -- e.g. no filings exist for this '
    'instrument in the window. Distinct from FAILED so that a resume does not '
    'retry it forever, and distinct from DONE so that gap reporting can tell '
    'the difference between "loaded" and "nothing to load".';
