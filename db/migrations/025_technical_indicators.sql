-- 025 · Technical indicators
--
-- Same posture as factor_scores (migration 024), not price_bars: this table
-- holds DERIVED values recomputed from price_bars on demand, not raw market
-- facts, so it is upsertable per (instrument_id, as_of, model_version) rather
-- than append-only. Re-running `technicals` after a price correction should
-- update the indicator, not accumulate a second stale version beside it.
--
-- Same licensing posture as everything built on price_bars: local research
-- only, not wired into the public web app or API, until a price source is
-- found whose terms actually permit public display. See
-- docs/03-data-sources.md §7.

CREATE TABLE technical_indicators (
    instrument_id  bigint      NOT NULL REFERENCES instruments (instrument_id),
    as_of          date        NOT NULL,
    model_version  text        NOT NULL,

    -- Every indicator by name, with its raw value and the window it was
    -- computed over -- the same provenance discipline as factor_scores and
    -- metric_values.inputs, applied to a third kind of derived figure.
    indicators     jsonb       NOT NULL,

    computed_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_technical_indicators UNIQUE (instrument_id, as_of, model_version)
);

COMMENT ON TABLE technical_indicators IS
    'Point-in-time technical readouts (SMA, RSI, MACD, Bollinger %B, volume '
    'ratio, rolling support/resistance). A calculation over historical '
    'prices, not a signal to act on -- see analytics/technicals.py.';

CREATE INDEX idx_technical_indicators_as_of ON technical_indicators (as_of);

-- ii_app already holds blanket SELECT on every table in public (migration
-- 007). Learned the hard way on migration 024: granting it INSERT here too
-- would be the same mistake test_no_role_holds_a_privilege_nobody_decided_on
-- caught then -- the serving role must never be able to write.
GRANT INSERT, UPDATE ON technical_indicators TO ii_ingest;
