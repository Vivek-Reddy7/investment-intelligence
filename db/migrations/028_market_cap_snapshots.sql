-- 028 · Market cap snapshots
--
-- LOCAL RESEARCH ONLY, same posture as price_bars/factor_scores/
-- technical_indicators (024/025) and for the same reason: market cap is
-- shares outstanding times PRICE, and price here is yfinance-sourced.
-- Unlike sector_classifications (027) beside it, this table cannot be
-- served publicly without the same source substitution those three need.
-- See docs/03-data-sources.md §7.
--
-- No cap-tier label (large/mid/small) is stored here, on purpose. India's
-- actual convention (SEBI) ranks companies by market cap across the WHOLE
-- listed universe -- top 100 is large-cap, 101-250 mid-cap, the rest
-- small-cap -- and this project tracks nine companies, not the market.
-- Applying "large/mid/small" to a nine-company sample would be a fabricated
-- label wearing a real classification's name, the same category of mistake
-- factor_score.py's docstring already warns against for a different reason.
-- What is stored is the number; the label is left undone rather than faked.

CREATE TABLE market_cap_snapshots (
    instrument_id       bigint       NOT NULL REFERENCES instruments (instrument_id),
    as_of                date         NOT NULL,
    shares_outstanding   bigint       NOT NULL,
    -- The filing date the share count itself was reported as of, which is
    -- USUALLY not as_of -- share counts are disclosed on filing cover pages
    -- and go stale between filings. Kept alongside the number so a reader
    -- can see how old the share count is, not just how old the price is.
    shares_as_of         date         NOT NULL,
    price                numeric(18,4) NOT NULL,
    currency             char(3)      NOT NULL,
    market_cap           numeric(24,4) NOT NULL,
    computed_at          timestamptz  NOT NULL DEFAULT now(),

    CONSTRAINT uq_market_cap_snapshots UNIQUE (instrument_id, as_of),
    CONSTRAINT ck_market_cap_positive CHECK (shares_outstanding > 0 AND price > 0)
);

COMMENT ON TABLE market_cap_snapshots IS
    'shares_outstanding * price, local research only -- price is '
    'yfinance-sourced (docs/03-data-sources.md §7). Deliberately carries no '
    'large/mid/small-cap label: that classification is rank-based against '
    'the full Indian listed universe and cannot be honestly produced from '
    'nine tracked companies.';

CREATE INDEX idx_market_cap_snapshots_as_of ON market_cap_snapshots (as_of);

GRANT SELECT ON market_cap_snapshots TO ii_app, ii_ingest;
GRANT INSERT, UPDATE ON market_cap_snapshots TO ii_ingest;
