-- 029 · Backtest results
--
-- LOCAL RESEARCH ONLY, same reason as price_bars/factor_scores/
-- technical_indicators/market_cap_snapshots: forward_return is computed
-- from yfinance-sourced prices. See docs/03-data-sources.md §7.
--
-- The one deliberate exception to the point-in-time rule everything else in
-- this project enforces: forward_return uses prices AFTER as_of, on
-- purpose. That is not a violation of the discipline -- it is the entire
-- point of a backtest. The composite_score being validated is still
-- computed using only information knowable ON as_of (analytics/backtest.py
-- calls combined_score.compute_scores exactly as `make combined` does,
-- just for a historical date); only the score's later OUTCOME is allowed
-- to look forward, because you cannot grade a prediction without eventually
-- comparing it to what happened.

CREATE TABLE backtest_results (
    instrument_id    bigint      NOT NULL REFERENCES instruments (instrument_id),
    as_of            date        NOT NULL,
    model_version    text        NOT NULL,
    composite_score  numeric(5,4) NOT NULL,
    forward_days     integer     NOT NULL,
    forward_return   numeric(10,6) NOT NULL,
    computed_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_backtest_results UNIQUE (instrument_id, as_of, model_version, forward_days)
);

COMMENT ON TABLE backtest_results IS
    'composite_score as of a past date, paired with the ACTUAL forward '
    'return over forward_days -- the one place this project deliberately '
    'looks forward, in order to grade a score against what happened. Local '
    'research only: forward_return is yfinance-derived.';

CREATE INDEX idx_backtest_results_model ON backtest_results (model_version, forward_days);

GRANT SELECT ON backtest_results TO ii_app, ii_ingest;
GRANT INSERT, UPDATE ON backtest_results TO ii_ingest;
