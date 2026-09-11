-- 013 · Derived metrics, with provenance, computed as of a date
--
-- Three things decided here, each of which could have been done the easy way
-- and been wrong.
--
-- 1. ONE DEFINITION. The metric formulas live in `metrics_as_of()` and
--    nowhere else. The materialised current view is populated by calling that
--    same function with now(). If the live view and the historical view had
--    separate definitions they would drift, and the drift would show up as a
--    screen that gives different answers depending on which date you asked
--    about -- which is indistinguishable from the product working.
--
-- 2. CURRENCY IS NOT OPTIONAL. Numerator and denominator must be in the SAME
--    currency. Infosys reports revenue in USD and INR; a net margin computed
--    from INR profit over USD revenue is a plausible-looking number that
--    means nothing. Metrics are therefore computed per currency.
--
-- 3. AN UNDEFINED RATIO IS NULL, NOT A NUMBER. Return on equity with negative
--    equity, growth from a negative base, a margin on zero revenue: these are
--    undefined, not large. Screeners that emit them produce rankings led by
--    companies whose figures are broken. Every denominator here is guarded.

CREATE TABLE metrics (
    metric_code   text    PRIMARY KEY,
    label         text    NOT NULL,
    definition    text    NOT NULL,   -- human-readable formula, for the UI
    family        text    NOT NULL
        CHECK (family IN ('PROFITABILITY', 'LEVERAGE', 'GROWTH', 'CASH')),
    unit          text    NOT NULL CHECK (unit IN ('RATIO', 'PERCENT')),
    higher_is_better boolean,         -- null where it genuinely depends
    display_order integer NOT NULL
);

INSERT INTO metrics (metric_code, label, definition, family, unit, higher_is_better, display_order) VALUES
 ('NET_MARGIN',      'Net profit margin',    'NET_PROFIT / REVENUE',                       'PROFITABILITY', 'PERCENT', true,  10),
 ('ROE',             'Return on equity',     'NET_PROFIT / TOTAL_EQUITY',                  'PROFITABILITY', 'PERCENT', true,  20),
 ('ROA',             'Return on assets',     'NET_PROFIT / TOTAL_ASSETS',                  'PROFITABILITY', 'PERCENT', true,  30),
 ('EFFECTIVE_TAX',   'Effective tax rate',   'TAX_EXPENSE / PROFIT_BEFORE_TAX',            'PROFITABILITY', 'PERCENT', null,  40),
 ('LIAB_TO_EQUITY',  'Liabilities to equity','(TOTAL_ASSETS - TOTAL_EQUITY) / TOTAL_EQUITY','LEVERAGE',     'RATIO',   false, 50),
 ('EQUITY_RATIO',    'Equity to assets',     'TOTAL_EQUITY / TOTAL_ASSETS',                'LEVERAGE',      'PERCENT', true,  60),
 ('REVENUE_GROWTH',  'Revenue growth YoY',   'REVENUE / prior-year REVENUE - 1',           'GROWTH',        'PERCENT', true,  70),
 ('PROFIT_GROWTH',   'Net profit growth YoY','NET_PROFIT / prior-year NET_PROFIT - 1',     'GROWTH',        'PERCENT', true,  80),
 ('CASH_CONVERSION', 'Operating cash conversion', 'CF_OPERATING / NET_PROFIT',             'CASH',          'PERCENT', true,  90);

COMMENT ON COLUMN metrics.definition IS
    'Shown to the reader. The Phase 1 promise is that a number can be traced '
    'to how it was derived, and that starts with saying so in words.';

-- ---------------------------------------------------------------------------
-- metrics_as_of: the single definition
-- ---------------------------------------------------------------------------
-- `inputs` carries the fact_ids the value was computed from. That is the
-- provenance chain's missing link: metric -> facts -> filing -> source ->
-- licence. A metric that cannot name its inputs is a defect, not a display
-- problem, so the column is NOT NULL by construction.
CREATE FUNCTION metrics_as_of(p_as_of timestamptz DEFAULT now())
RETURNS TABLE (
    instrument_id bigint,
    basis         text,
    fiscal_year   smallint,
    period_type   text,
    currency      char(3),
    metric_code   text,
    value         numeric,
    inputs        bigint[]
)
LANGUAGE sql STABLE AS $$
    WITH f AS (
        SELECT * FROM facts_as_of(p_as_of)
    ),
    -- Pivot to one row per (instrument, basis, period, currency). Currency is
    -- in the grouping key on purpose: see the header note.
    wide AS (
        SELECT
            f.instrument_id, f.basis, f.fiscal_year, f.period_type, f.currency,
            max(f.value)   FILTER (WHERE f.line_item = 'REVENUE')           AS revenue,
            max(f.fact_id) FILTER (WHERE f.line_item = 'REVENUE')           AS revenue_id,
            max(f.value)   FILTER (WHERE f.line_item = 'NET_PROFIT')        AS net_profit,
            max(f.fact_id) FILTER (WHERE f.line_item = 'NET_PROFIT')        AS net_profit_id,
            max(f.value)   FILTER (WHERE f.line_item = 'PROFIT_BEFORE_TAX') AS pbt,
            max(f.fact_id) FILTER (WHERE f.line_item = 'PROFIT_BEFORE_TAX') AS pbt_id,
            max(f.value)   FILTER (WHERE f.line_item = 'TAX_EXPENSE')       AS tax,
            max(f.fact_id) FILTER (WHERE f.line_item = 'TAX_EXPENSE')       AS tax_id,
            max(f.value)   FILTER (WHERE f.line_item = 'TOTAL_ASSETS')      AS assets,
            max(f.fact_id) FILTER (WHERE f.line_item = 'TOTAL_ASSETS')      AS assets_id,
            max(f.value)   FILTER (WHERE f.line_item = 'TOTAL_EQUITY')      AS equity,
            max(f.fact_id) FILTER (WHERE f.line_item = 'TOTAL_EQUITY')      AS equity_id,
            max(f.value)   FILTER (WHERE f.line_item = 'CF_OPERATING')      AS cfo,
            max(f.fact_id) FILTER (WHERE f.line_item = 'CF_OPERATING')      AS cfo_id
        FROM f
        GROUP BY 1, 2, 3, 4, 5
    ),
    -- Prior year, for growth. Joined on the same basis, period type AND
    -- currency: comparing this year's INR revenue to last year's USD revenue
    -- would produce a growth rate that is purely an exchange-rate artefact.
    withprior AS (
        SELECT w.*,
               p.revenue    AS prior_revenue,
               p.revenue_id AS prior_revenue_id,
               p.net_profit AS prior_net_profit,
               p.net_profit_id AS prior_net_profit_id
        FROM   wide w
        LEFT   JOIN wide p
                 ON p.instrument_id = w.instrument_id
                AND p.basis         = w.basis
                AND p.period_type   = w.period_type
                AND p.currency      = w.currency
                AND p.fiscal_year   = w.fiscal_year - 1
    )
    SELECT instrument_id, basis, fiscal_year, period_type, currency,
           metric_code, value, inputs
    FROM withprior w
    CROSS JOIN LATERAL (VALUES
        -- Margins and returns. Denominators guarded with NULLIF for zero and
        -- an explicit sign test where a negative denominator makes the ratio
        -- meaningless rather than merely negative.
        ('NET_MARGIN',
         w.net_profit / nullif(w.revenue, 0),
         ARRAY[w.net_profit_id, w.revenue_id]),

        ('ROE',
         CASE WHEN w.equity > 0 THEN w.net_profit / w.equity END,
         ARRAY[w.net_profit_id, w.equity_id]),

        ('ROA',
         CASE WHEN w.assets > 0 THEN w.net_profit / w.assets END,
         ARRAY[w.net_profit_id, w.assets_id]),

        ('EFFECTIVE_TAX',
         CASE WHEN w.pbt > 0 THEN w.tax / w.pbt END,
         ARRAY[w.tax_id, w.pbt_id]),

        ('LIAB_TO_EQUITY',
         CASE WHEN w.equity > 0 THEN (w.assets - w.equity) / w.equity END,
         ARRAY[w.assets_id, w.equity_id]),

        ('EQUITY_RATIO',
         CASE WHEN w.assets > 0 THEN w.equity / w.assets END,
         ARRAY[w.equity_id, w.assets_id]),

        -- Growth from a negative or zero base is undefined, not large. A
        -- company going from -100 to +50 has not grown by -150%.
        ('REVENUE_GROWTH',
         CASE WHEN w.prior_revenue > 0
              THEN w.revenue / w.prior_revenue - 1 END,
         ARRAY[w.revenue_id, w.prior_revenue_id]),

        ('PROFIT_GROWTH',
         CASE WHEN w.prior_net_profit > 0
              THEN w.net_profit / w.prior_net_profit - 1 END,
         ARRAY[w.net_profit_id, w.prior_net_profit_id]),

        -- Cash conversion against a loss is not informative.
        ('CASH_CONVERSION',
         CASE WHEN w.net_profit > 0 THEN w.cfo / w.net_profit END,
         ARRAY[w.cfo_id, w.net_profit_id])
    ) AS m(metric_code, value, inputs)
    WHERE value IS NOT NULL
      -- A metric whose inputs are not all present was computed from a NULL
      -- and would be a silent lie. Belt and braces with the guards above.
      AND array_position(inputs, NULL) IS NULL;
$$;

COMMENT ON FUNCTION metrics_as_of(timestamptz) IS
    'Every derived metric, computed from the facts as known at p_as_of. The '
    'single definition: the materialised current view is this function called '
    'with now(). Returns nothing where a metric is undefined.';

-- ---------------------------------------------------------------------------
-- Materialised current view, for screening the universe fast
-- ---------------------------------------------------------------------------
-- Architecture §2.12: materialise current, compute historical on demand.
-- Screening 500 companies on six metrics per request would recompute the
-- pivot every time, and the inputs only change once a day.
--
-- Not a MATERIALIZED VIEW, because we want `computed_at` per row -- the Phase
-- 1 promise includes saying when a number was computed, not only from what.
CREATE TABLE metric_values (
    instrument_id bigint      NOT NULL REFERENCES instruments (instrument_id),
    basis         text        NOT NULL,
    fiscal_year   smallint    NOT NULL,
    period_type   text        NOT NULL,
    currency      char(3)     NOT NULL,
    metric_code   text        NOT NULL REFERENCES metrics (metric_code),
    value         numeric     NOT NULL,
    inputs        bigint[]    NOT NULL CHECK (array_length(inputs, 1) > 0),
    computed_at   timestamptz NOT NULL DEFAULT now(),
    as_of         timestamptz NOT NULL,

    PRIMARY KEY (instrument_id, basis, fiscal_year, period_type, currency, metric_code)
);

COMMENT ON COLUMN metric_values.as_of IS
    'The as-of date this row was computed for. Distinct from computed_at: a '
    'row may be recomputed today (computed_at) for the world as known today '
    '(as_of). Both are needed to explain a number.';

CREATE INDEX idx_metric_values_screen
    ON metric_values (metric_code, currency, fiscal_year, value);

-- Derived and recomputable, so replacing it wholesale is correct -- it is not
-- a fact and carries no history. That is exactly why it is DELETE-able while
-- financial_facts is not.
CREATE FUNCTION refresh_metric_values() RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
    stamp timestamptz := now();
BEGIN
    DELETE FROM metric_values;
    INSERT INTO metric_values (instrument_id, basis, fiscal_year, period_type,
                               currency, metric_code, value, inputs,
                               computed_at, as_of)
    SELECT instrument_id, basis, fiscal_year, period_type, currency,
           metric_code, value, inputs, stamp, stamp
    FROM   metrics_as_of(stamp);
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$;

GRANT SELECT ON metrics, metric_values TO ii_app, ii_ingest;
GRANT EXECUTE ON FUNCTION metrics_as_of(timestamptz) TO ii_app, ii_ingest;
GRANT EXECUTE ON FUNCTION refresh_metric_values() TO ii_ingest;
GRANT INSERT, DELETE ON metric_values TO ii_ingest;
