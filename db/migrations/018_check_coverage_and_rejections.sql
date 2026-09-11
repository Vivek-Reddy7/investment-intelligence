-- 018 · Check coverage, and persisting rejections
--
-- Two gaps found by actually running Phase 17's checks.
--
-- GAP 1: a check that never fires looks exactly like a check that passes.
--
-- The first quality run reported zero TAX_IDENTITY and zero FX_INCONSISTENT
-- findings. That is either "the data is sound" or "those checks had no inputs
-- and silently evaluated nothing", and the report could not tell them apart.
-- It took a hand-written query to establish that 183 of 185 periods really
-- were checked, which made the zero meaningful.
--
-- A quality report that cannot distinguish clean data from an inert check is
-- worse than no report, because it is actively reassuring. So coverage is
-- reported alongside findings, always.
--
-- GAP 2: rejections were counted and their reasons thrown away.
--
-- The Phase 7 backfill rejected 372 items. `ingestion_runs.rows_rejected`
-- recorded the number; the reasons lived only in a `BackfillReport` that went
-- out of scope. So we knew we were discarding data and not what, or why. That
-- is not a quality report, it is an admission with no detail.

CREATE FUNCTION quality_coverage(p_as_of timestamptz DEFAULT now())
RETURNS TABLE (
    check_code text,
    severity   text,
    evaluable  bigint,
    findings   bigint
)
LANGUAGE sql STABLE AS $$
    WITH wide AS (
        SELECT
            f.instrument_id, f.basis, f.fiscal_year, f.period_type, f.currency,
            max(f.value) FILTER (WHERE f.line_item = 'REVENUE')           AS revenue,
            max(f.value) FILTER (WHERE f.line_item = 'NET_PROFIT')        AS net_profit,
            max(f.value) FILTER (WHERE f.line_item = 'PROFIT_BEFORE_TAX') AS pbt,
            max(f.value) FILTER (WHERE f.line_item = 'TAX_EXPENSE')       AS tax,
            max(f.value) FILTER (WHERE f.line_item = 'TOTAL_ASSETS')      AS assets,
            max(f.value) FILTER (WHERE f.line_item = 'TOTAL_EQUITY')      AS equity
        FROM   facts_as_of(p_as_of) f
        GROUP  BY 1, 2, 3, 4, 5
    ),
    -- How many periods each check was actually able to evaluate. The
    -- conditions here MUST mirror the NOT NULL requirements in
    -- quality_as_of(), or coverage overstates what was examined.
    evaluable AS (
        SELECT 'TAX_IDENTITY' AS check_code, count(*) AS n FROM wide
         WHERE pbt IS NOT NULL AND tax IS NOT NULL AND net_profit IS NOT NULL
           AND abs(pbt) > 0
        UNION ALL
        SELECT 'EQUITY_EXCEEDS_ASSETS', count(*) FROM wide
         WHERE equity IS NOT NULL AND assets IS NOT NULL
        UNION ALL
        SELECT 'NEGATIVE_REVENUE', count(*) FROM wide WHERE revenue IS NOT NULL
        UNION ALL
        SELECT 'INCOMPLETE_PERIOD', count(*) FROM wide WHERE revenue IS NOT NULL
        UNION ALL
        SELECT 'IMPLAUSIBLE_TAX_RATE', count(*) FROM wide
         WHERE pbt > 0 AND tax IS NOT NULL
        UNION ALL
        SELECT 'FX_INCONSISTENT', count(*)
          FROM wide a JOIN wide b
                 ON  b.instrument_id = a.instrument_id AND b.basis = a.basis
                AND  b.fiscal_year   = a.fiscal_year   AND b.period_type = a.period_type
                AND  b.currency      < a.currency
         WHERE a.revenue IS NOT NULL AND b.revenue IS NOT NULL
           AND a.net_profit IS NOT NULL AND b.net_profit IS NOT NULL
           AND b.revenue <> 0 AND b.net_profit <> 0
    )
    SELECT c.check_code, c.severity, coalesce(e.n, 0),
           coalesce(f.n, 0)
    FROM   quality_checks c
    LEFT   JOIN evaluable e USING (check_code)
    LEFT   JOIN LATERAL (
               SELECT count(*) AS n FROM quality_as_of(p_as_of) q
               WHERE  q.check_code = c.check_code
           ) f ON true
    ORDER  BY c.severity, c.check_code;
$$;

COMMENT ON FUNCTION quality_coverage(timestamptz) IS
    'Findings AND how many periods each check could evaluate. Zero findings '
    'over zero evaluable periods is an inert check, not clean data, and the '
    'two must never be reported the same way.';

-- ---------------------------------------------------------------------------
-- Rejections, kept
-- ---------------------------------------------------------------------------
-- Operational, derived from a run, and deliberately mutable: a re-run of the
-- same window supersedes the previous verdict.
CREATE TABLE ingestion_rejections (
    rejection_id  bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id        bigint      NOT NULL REFERENCES ingestion_runs (run_id),
    source_id     text        NOT NULL REFERENCES sources (source_id),
    instrument_ref text       NOT NULL,
    detail        text        NOT NULL,
    reason        text        NOT NULL,
    -- The reason with specifics stripped, so 372 individual rejections
    -- collapse into the handful of KINDS that actually need decisions.
    reason_class  text        NOT NULL,
    occurred_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_rejections_class ON ingestion_rejections (reason_class, source_id);
CREATE INDEX idx_rejections_run ON ingestion_rejections (run_id);

COMMENT ON COLUMN ingestion_rejections.reason_class IS
    'Coarse category. Individual reasons carry values and dates, so grouping '
    'on the raw text produces 372 groups of one. The class is what turns a '
    'count into a decision: is this data we should be parsing, or data that '
    'genuinely does not belong?';

GRANT SELECT ON ingestion_rejections TO ii_app, ii_ingest;
GRANT INSERT, DELETE ON ingestion_rejections TO ii_ingest;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ii_ingest;
GRANT EXECUTE ON FUNCTION quality_coverage(timestamptz) TO ii_app, ii_ingest;
