-- 031 · Analytics runs
--
-- The local factor-score/technicals/combined/sectors/market-cap/backtest/
-- risk pipeline (migrations 023-030) has been invisible to observability
-- from the day it was built: `ingestion_runs` and everything downstream of
-- it (`ingestion_health()`, `ingestion_metrics`, the operational-alerts
-- detector, `make status`) only ever sees the EDGAR fundamentals job,
-- because that is the only thing Phase 8/16 existed to watch. Nothing
-- records that `make technicals` ran, or when it last succeeded, or that
-- yesterday's `make backtest` crashed halfway through -- someone running
-- this pipeline manually against a nine-company universe would only find
-- out by reading the report and noticing a number looks stale.
--
-- Not reusing `ingestion_runs` for this. That table's shape is built
-- around FETCHING FROM A SOURCE: `source_id` is a NOT NULL foreign key
-- into `sources`, and `kind` is constrained to
-- ('INSTRUMENTS','FUNDAMENTALS','CORPORATE_ACTIONS'). Five of these six
-- jobs are internal recomputation over data already sitting in this
-- database (technicals/risk/combined/backtest read price_bars and
-- financial_facts we already have; only `sectors` and `marketcap` reach
-- back out to EDGAR, and even they do it per-instrument inside an
-- existing job rather than as the job's own identity). Bending
-- `ingestion_runs`'s CHECK and FK to fit would blur a real distinction --
-- "we fetched something new" vs "we recomputed something from what we
-- already have" -- the same kind of type-fit mistake `analytics/
-- classification.py`'s docstring already declined to make for
-- `ReportedFact`.
--
-- `as_of` is nullable rather than NOT NULL: a `backtest` run spans a whole
-- list of historical checkpoint dates, not one `as_of`, so it is left null
-- there and populated for the other five jobs.
--
-- No FK to `sources`, no `window_start`/`window_end` -- this job shape has
-- neither. Otherwise deliberately parallel to `ingestion_runs`
-- (RUNNING/SUCCESS/PARTIAL/FAILED, a failed run must explain itself, a
-- finished run must have an end) so the two are read the same way once
-- someone already knows how to read one.

CREATE TABLE analytics_runs (
    run_id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job             text        NOT NULL
        CHECK (job IN ('TECHNICALS', 'RISK', 'COMBINED_SCORE', 'SECTORS',
                        'MARKET_CAP', 'BACKTEST')),
    as_of           date,

    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    outcome         text        NOT NULL DEFAULT 'RUNNING'
        CHECK (outcome IN ('RUNNING', 'SUCCESS', 'PARTIAL', 'FAILED')),

    rows_written    integer     NOT NULL DEFAULT 0 CHECK (rows_written >= 0),
    error           text,

    CONSTRAINT finished_runs_have_an_end
        CHECK (outcome = 'RUNNING' OR finished_at IS NOT NULL),
    CONSTRAINT failed_runs_explain_themselves
        CHECK (outcome <> 'FAILED' OR error IS NOT NULL)
);

-- Supports "when did `job` last succeed" the same way
-- idx_runs_source_kind_started supports it for ingestion_runs.
CREATE INDEX idx_analytics_runs_job_started
    ON analytics_runs (job, started_at DESC);

COMMENT ON TABLE analytics_runs IS
    'Run log for the local-only analytics pipeline (technicals, risk, '
    'combined score, sectors, market cap, backtest) -- deliberately separate '
    'from ingestion_runs, which is shaped around fetching from an external '
    'source rather than recomputing from data already stored. Same '
    'local-research-only boundary as the tables these jobs write to '
    '(docs/03-data-sources.md §7): the boundary is enforced by never '
    'querying it from web/, not by a grant -- ii_web already inherits '
    'ii_app''s SELECT here the same way it does on price_bars and '
    'technical_indicators.';

GRANT SELECT ON analytics_runs TO ii_app, ii_ingest;
GRANT INSERT, UPDATE ON analytics_runs TO ii_ingest;
