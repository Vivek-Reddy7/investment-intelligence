-- 030 · Risk metrics
--
-- LOCAL RESEARCH ONLY, same reason as price_bars/factor_scores/
-- technical_indicators/market_cap_snapshots/backtest_results: every metric
-- here is derived from yfinance-sourced closes. See docs/03-data-sources.md
-- §7.
--
-- A separate table from technical_indicators on purpose, matching the
-- distinction the original brief itself draws: technicals answer "what is
-- the price doing" (trend, momentum, overbought/oversold); risk metrics
-- answer "how much could this move against you" -- a different question,
-- answered from the same underlying prices but not the same kind of
-- number, and conflating the two tables would blur a distinction worth
-- keeping.

CREATE TABLE risk_metrics (
    instrument_id    bigint      NOT NULL REFERENCES instruments (instrument_id),
    as_of            date        NOT NULL,
    model_version    text        NOT NULL,
    metrics          jsonb       NOT NULL,
    computed_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_risk_metrics UNIQUE (instrument_id, as_of, model_version)
);

COMMENT ON TABLE risk_metrics IS
    'Annualised volatility, max drawdown, historical 95% VaR -- point-in-time '
    'from price_bars, local research only. A description of past variability, '
    'not a prediction and not a recommendation.';

CREATE INDEX idx_risk_metrics_as_of ON risk_metrics (as_of);

GRANT SELECT ON risk_metrics TO ii_app, ii_ingest;
GRANT INSERT, UPDATE ON risk_metrics TO ii_ingest;
