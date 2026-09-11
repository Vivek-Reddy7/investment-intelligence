-- 001 · Sources and the ingestion run log
--
-- `sources` encodes the Phase 3 discipline into the schema. Every fact must
-- name the source it came from, and a source row cannot exist without a
-- licence note and the date we verified it. So an adapter that nobody has
-- checked the terms of cannot be wired into ingestion: there is no source row
-- for it, and the foreign key refuses the fact.
--
-- `ingestion_runs` is the run log from architecture §2.5. The rule that makes
-- it useful: a failed run is a ROW, not an absence. Without that, a scheduler
-- that never fired and a run with nothing to do look identical -- and
-- GitHub Actions silently disabling a schedule after 60 days (ADR 004) is a
-- real, documented instance of exactly that.

CREATE TABLE sources (
    source_id       text        PRIMARY KEY,
    name            text        NOT NULL,
    kind            text        NOT NULL
        CHECK (kind IN ('FILINGS', 'EXCHANGE_DISCLOSURE', 'COMPANY_WEBSITE',
                        'BROKER', 'MANUAL')),

    -- Licence position. Not decorative: see the comment above.
    licence_note    text        NOT NULL CHECK (length(btrim(licence_note)) > 0),
    licence_url     text,
    redistributable boolean     NOT NULL,
    verified_on     date        NOT NULL,

    created_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN sources.redistributable IS
    'True only if we have verified we may display this data publicly. '
    'Phase 3: NSE market data is false at any price for simulation use, and '
    'costs Rs 1,10,000 per display medium otherwise.';

CREATE TABLE ingestion_runs (
    run_id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id       text        NOT NULL REFERENCES sources (source_id),
    kind            text        NOT NULL
        CHECK (kind IN ('INSTRUMENTS', 'FUNDAMENTALS', 'CORPORATE_ACTIONS')),

    -- What the run was asked to cover. Null for event-shaped runs that
    -- discover their own work (fundamentals arrive when companies file,
    -- not on a calendar).
    window_start    date,
    window_end      date,

    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    outcome         text        NOT NULL DEFAULT 'RUNNING'
        CHECK (outcome IN ('RUNNING', 'SUCCESS', 'PARTIAL', 'FAILED')),

    rows_written    integer     NOT NULL DEFAULT 0 CHECK (rows_written  >= 0),
    rows_rejected   integer     NOT NULL DEFAULT 0 CHECK (rows_rejected >= 0),
    error           text,

    CONSTRAINT finished_runs_have_an_end
        CHECK (outcome = 'RUNNING' OR finished_at IS NOT NULL),
    CONSTRAINT failed_runs_explain_themselves
        CHECK (outcome <> 'FAILED' OR error IS NOT NULL)
);

-- Supports the gap-detection query: "when did this source/kind last succeed?"
CREATE INDEX idx_runs_source_kind_started
    ON ingestion_runs (source_id, kind, started_at DESC);
