-- 017 · Data quality checks
--
-- For a product whose entire claim is that its history is trustworthy, silent
-- corruption is the worst available failure. Everything else in the roadmap
-- guards the STORE -- append-only, versioning, provenance. Nothing yet guards
-- the CONTENT: a mis-mapped XBRL concept produces a perfectly well-formed
-- fact with a plausible value, stored correctly, traceable to a real filing,
-- and wrong.
--
-- The edgar.py CONCEPTS map has a comment saying exactly this: "a wrong claim
-- here is invisible in the output -- it produces a plausible number computed
-- from the wrong input." These checks are the attempt to make it visible.
--
-- The approach is accounting identities. A financial statement is internally
-- redundant: profit before tax minus tax equals profit after tax, assets
-- cannot be less than equity, and a company reporting in two currencies
-- implies one exchange rate across every line item. Those redundancies are
-- free error detection, and they need no second source.
--
-- Severity matters. ERROR means the data cannot be right. WARN means it is
-- unusual and a human should look. Emitting everything as ERROR trains people
-- to ignore the report, which is worse than having no report.

CREATE TABLE quality_checks (
    check_code  text PRIMARY KEY,
    label       text NOT NULL,
    rationale   text NOT NULL,
    severity    text NOT NULL CHECK (severity IN ('ERROR', 'WARN')),
    tolerance   numeric
);

INSERT INTO quality_checks (check_code, label, rationale, severity, tolerance) VALUES
 ('TAX_IDENTITY', 'Profit before tax minus tax should equal net profit',
  'The strongest available single-period identity. A material break usually '
  'means a concept was mapped to the wrong line item, which is the failure '
  'mode the concept map cannot detect on its own. Tolerance allows for '
  'minority interests and discontinued operations, which legitimately break it.',
  'WARN', 0.02),

 ('EQUITY_EXCEEDS_ASSETS', 'Equity cannot exceed total assets',
  'Would require negative liabilities. There is no legitimate reading of this, '
  'so it is an ERROR rather than something to review.',
  'ERROR', NULL),

 ('NEGATIVE_REVENUE', 'Revenue should not be negative',
  'Possible in principle via returns exceeding sales, and in practice a sign '
  'error or a sign-flipped concept.',
  'ERROR', NULL),

 ('FX_INCONSISTENT', 'One period reported in two currencies implies one rate',
  'If a company reports the same period in INR and USD, revenue/revenue and '
  'profit/profit must imply the same exchange rate. Divergence means one of '
  'the two figures is mismatched -- a different period, a different basis, or '
  'a mismapped concept. This is cross-validation without a second source.',
  'WARN', 0.05),

 ('INCOMPLETE_PERIOD', 'A period with revenue but no profit figure',
  'Ingestion found part of a statement. Not wrong, but every profitability '
  'metric for that period is silently absent rather than visibly missing.',
  'WARN', NULL),

 ('IMPLAUSIBLE_TAX_RATE', 'Effective tax rate outside 0-60%',
  'Legitimate outside that band (tax credits, prior-year adjustments) but '
  'also what a swapped numerator and denominator looks like.',
  'WARN', NULL);

-- ---------------------------------------------------------------------------
-- Findings
-- ---------------------------------------------------------------------------
-- Derived and recomputable, like metric_values, so replacing it wholesale is
-- correct. It records no history and losing it costs nothing -- which is
-- precisely why it may be deleted while financial_facts may not.
CREATE TABLE quality_findings (
    finding_id    bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    check_code    text        NOT NULL REFERENCES quality_checks (check_code),
    instrument_id bigint      NOT NULL REFERENCES instruments (instrument_id),
    basis         text        NOT NULL,
    fiscal_year   smallint    NOT NULL,
    period_type   text        NOT NULL,
    currency      char(3),
    detail        text        NOT NULL,
    observed      numeric,
    expected      numeric,
    -- Which facts are implicated, so a finding is actionable rather than an
    -- accusation. Same provenance mechanism as metric_values.inputs.
    inputs        bigint[]    NOT NULL,
    as_of         timestamptz NOT NULL,
    computed_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_findings_check ON quality_findings (check_code, instrument_id);

-- ---------------------------------------------------------------------------
-- The checks, as one function
-- ---------------------------------------------------------------------------
-- As-of dated like everything else (invariant 12), so quality can be assessed
-- for a past date too: "was the data we were serving in 2020 sound?" is a
-- question this product should be able to answer about itself.
CREATE FUNCTION quality_as_of(p_as_of timestamptz DEFAULT now())
RETURNS TABLE (
    check_code    text,
    instrument_id bigint,
    basis         text,
    fiscal_year   smallint,
    period_type   text,
    currency      char(3),
    detail        text,
    observed      numeric,
    expected      numeric,
    inputs        bigint[]
)
LANGUAGE sql STABLE AS $$
    WITH wide AS (
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
            max(f.fact_id) FILTER (WHERE f.line_item = 'TOTAL_EQUITY')      AS equity_id
        FROM   facts_as_of(p_as_of) f
        GROUP  BY 1, 2, 3, 4, 5
    ),
    tol AS (SELECT check_code, tolerance FROM quality_checks)

    -- PBT - TAX = NET_PROFIT
    SELECT 'TAX_IDENTITY', w.instrument_id, w.basis, w.fiscal_year,
           w.period_type, w.currency,
           format('pbt %s - tax %s = %s, but net profit is %s',
                  w.pbt, w.tax, w.pbt - w.tax, w.net_profit),
           abs((w.pbt - w.tax) - w.net_profit) / abs(w.pbt),
           (SELECT tolerance FROM tol WHERE check_code = 'TAX_IDENTITY'),
           ARRAY[w.pbt_id, w.tax_id, w.net_profit_id]
    FROM   wide w
    WHERE  w.pbt IS NOT NULL AND w.tax IS NOT NULL AND w.net_profit IS NOT NULL
      AND  abs(w.pbt) > 0
      AND  abs((w.pbt - w.tax) - w.net_profit) / abs(w.pbt)
             > (SELECT tolerance FROM tol WHERE check_code = 'TAX_IDENTITY')

    UNION ALL

    SELECT 'EQUITY_EXCEEDS_ASSETS', w.instrument_id, w.basis, w.fiscal_year,
           w.period_type, w.currency,
           format('equity %s exceeds total assets %s', w.equity, w.assets),
           w.equity - w.assets, 0,
           ARRAY[w.equity_id, w.assets_id]
    FROM   wide w
    WHERE  w.equity IS NOT NULL AND w.assets IS NOT NULL
      AND  w.equity > w.assets

    UNION ALL

    SELECT 'NEGATIVE_REVENUE', w.instrument_id, w.basis, w.fiscal_year,
           w.period_type, w.currency,
           format('revenue is %s', w.revenue),
           w.revenue, 0, ARRAY[w.revenue_id]
    FROM   wide w
    WHERE  w.revenue < 0

    UNION ALL

    SELECT 'INCOMPLETE_PERIOD', w.instrument_id, w.basis, w.fiscal_year,
           w.period_type, w.currency,
           'revenue present but no net profit figure for this period',
           NULL, NULL, ARRAY[w.revenue_id]
    FROM   wide w
    WHERE  w.revenue IS NOT NULL AND w.net_profit IS NULL

    UNION ALL

    SELECT 'IMPLAUSIBLE_TAX_RATE', w.instrument_id, w.basis, w.fiscal_year,
           w.period_type, w.currency,
           format('effective tax rate %s on pbt %s',
                  round(w.tax / w.pbt, 4), w.pbt),
           w.tax / w.pbt, NULL, ARRAY[w.tax_id, w.pbt_id]
    FROM   wide w
    WHERE  w.pbt > 0 AND w.tax IS NOT NULL
      AND  (w.tax / w.pbt < 0 OR w.tax / w.pbt > 0.60)

    UNION ALL

    -- Cross-currency consistency. No second source needed: a company that
    -- reports one period in two currencies has told us the same story twice,
    -- and the implied exchange rate must agree across line items.
    SELECT 'FX_INCONSISTENT', a.instrument_id, a.basis, a.fiscal_year,
           a.period_type, NULL::char(3),
           format('%s/%s implies %s on revenue but %s on net profit',
                  a.currency, b.currency,
                  round(a.revenue / b.revenue, 4),
                  round(a.net_profit / b.net_profit, 4)),
           abs((a.revenue / b.revenue) - (a.net_profit / b.net_profit))
             / (a.revenue / b.revenue),
           (SELECT tolerance FROM tol WHERE check_code = 'FX_INCONSISTENT'),
           ARRAY[a.revenue_id, b.revenue_id, a.net_profit_id, b.net_profit_id]
    FROM   wide a
    JOIN   wide b
             ON  b.instrument_id = a.instrument_id
            AND  b.basis         = a.basis
            AND  b.fiscal_year   = a.fiscal_year
            AND  b.period_type   = a.period_type
            AND  b.currency      < a.currency   -- each pair once
    WHERE  a.revenue    IS NOT NULL AND b.revenue    IS NOT NULL
      AND  a.net_profit IS NOT NULL AND b.net_profit IS NOT NULL
      AND  b.revenue <> 0 AND b.net_profit <> 0
      AND  a.revenue / b.revenue > 0 AND a.net_profit / b.net_profit > 0
      AND  abs((a.revenue / b.revenue) - (a.net_profit / b.net_profit))
             / (a.revenue / b.revenue)
             > (SELECT tolerance FROM tol WHERE check_code = 'FX_INCONSISTENT');
$$;

COMMENT ON FUNCTION quality_as_of(timestamptz) IS
    'Every quality finding, assessed against the facts as known at p_as_of. '
    'As-of dated like everything else, so "was the data we served in 2020 '
    'sound?" is answerable.';

CREATE FUNCTION refresh_quality_findings() RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE n integer; stamp timestamptz := now();
BEGIN
    DELETE FROM quality_findings;
    INSERT INTO quality_findings (check_code, instrument_id, basis, fiscal_year,
                                  period_type, currency, detail, observed,
                                  expected, inputs, as_of, computed_at)
    SELECT check_code, instrument_id, basis, fiscal_year, period_type, currency,
           detail, observed, expected, inputs, stamp, stamp
    FROM   quality_as_of(stamp);
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$;

GRANT SELECT ON quality_checks, quality_findings TO ii_app, ii_ingest;
GRANT EXECUTE ON FUNCTION quality_as_of(timestamptz) TO ii_app, ii_ingest;
GRANT EXECUTE ON FUNCTION refresh_quality_findings() TO ii_ingest;
GRANT INSERT, DELETE ON quality_findings TO ii_ingest;
