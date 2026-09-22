-- 024 · Daily price bars and factor scores
--
-- Two tables, and neither is bitemporal the way financial_facts is, for a
-- reason worth stating: a filed financial figure can be RESTATED after the
-- fact -- the company itself changes its mind about last year's revenue,
-- months later. A daily closing price cannot. Once 2026-06-30's close is
-- printed, it is printed forever; there is no issuer to restate it. So
-- price_bars has no known_from and no version history -- (instrument_id, day)
-- is simply unique, and a re-fetch either matches what is stored or is a data
-- error, never a legitimate second version.
--
-- factor_scores IS point-in-time in the sense that matters here: every score
-- is computed strictly from information dated on or before its own as_of
-- date, the same discipline financial_facts already enforces for filings.
-- The label used to VALIDATE the scoring approach (forward return) is
-- deliberately excluded from the stored row -- storing it here would make it
-- trivial to accidentally join a score against its own answer.
--
-- LICENSING -- two separate findings, and they point different directions.
--
-- 1. NSE's ₹1.1L/yr display tariff and clause 7.4 (docs/03-data-sources.md
--    §1) govern Indian-exchange-listed prices. They do not apply to this
--    table today, because the nine tracked companies are US-listed
--    (NYSE/NASDAQ ADRs, or a direct listing for GLOB) -- their prices are
--    ordinary US equity market data, not NSE data.
--
-- 2. That does NOT mean price data here is unrestricted. Checked 2026-09-22:
--    Yahoo's own Terms of Service prohibit automated access without written
--    permission and prohibit redistributing or monetising Yahoo API data
--    without a licence. Yahoo retired its official API in 2017; `yfinance`
--    (used by sources/prices.py, and by paper-trader) calls Yahoo's internal
--    endpoints without a licence at all. The risk is asymmetric: a private
--    local backtester (paper-trader's actual use) is low-risk personal
--    research use. A public, customer-facing product REDISPLAYING that data
--    -- which is exactly the shape of this table -- is the higher-risk case
--    Yahoo's own terms call out by name.
--
-- Consequence: the source row seeded for this table (see cli.py) is marked
-- redistributable = FALSE, and nothing built on top of it may be wired into
-- the public web app or API until either (a) a source with terms that
-- actually permit public display is substituted, matching the SEC_EDGAR
-- standard already met elsewhere in this project, or (b) that restriction is
-- knowingly accepted for a specific, disclosed reason. Absent either, this
-- table exists for local research and pipeline validation only -- the same
-- posture EDGAR-sourced fundamentals had before Phase 3 cleared them for
-- public use.

CREATE TABLE price_bars (
    instrument_id  bigint       NOT NULL REFERENCES instruments (instrument_id),
    day            date         NOT NULL,
    open           numeric(18,4) NOT NULL,
    high           numeric(18,4) NOT NULL,
    low            numeric(18,4) NOT NULL,
    close          numeric(18,4) NOT NULL,
    volume         bigint       NOT NULL,
    source_id      text         NOT NULL REFERENCES sources (source_id),
    ingested_at    timestamptz  NOT NULL DEFAULT now(),

    CONSTRAINT pk_price_bars PRIMARY KEY (instrument_id, day),
    CONSTRAINT ck_price_bars_high_low CHECK (high >= low),
    CONSTRAINT ck_price_bars_open_in_range CHECK (open BETWEEN low AND high),
    CONSTRAINT ck_price_bars_close_in_range CHECK (close BETWEEN low AND high),
    CONSTRAINT ck_price_bars_volume CHECK (volume >= 0)
);

COMMENT ON TABLE price_bars IS
    'Daily OHLCV, research/local use only until a redistributable price '
    'source is confirmed -- see this migration''s header. Not bitemporal: a '
    'printed close is not later restated, unlike a filed fact.';

CREATE INDEX idx_price_bars_day ON price_bars (day);

-- Same append-only posture as financial_facts, enforced the same way: by
-- trigger, so a dropped trigger does not silently reopen the table, and by
-- privilege, so the reverse is also true.
CREATE OR REPLACE FUNCTION reject_price_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'price_bars is append-only: % on (%, %) is not permitted. '
        'A new fetch upserts the same (instrument_id, day) if the vendor '
        'corrected a bad print; it does not go through UPDATE.',
        TG_OP, OLD.instrument_id, OLD.day;
END;
$$;

CREATE TRIGGER trg_price_bars_no_update
    BEFORE UPDATE ON price_bars
    FOR EACH ROW EXECUTE FUNCTION reject_price_mutation();

CREATE TRIGGER trg_price_bars_no_delete
    BEFORE DELETE ON price_bars
    FOR EACH ROW EXECUTE FUNCTION reject_price_mutation();

-- ---------------------------------------------------------------------------

-- 'FILINGS' does not describe a market-data vendor, and forcing yfinance's
-- source row into that category would misdescribe it right where the schema
-- exists specifically to describe licence positions honestly.
ALTER TABLE sources DROP CONSTRAINT sources_kind_check;
ALTER TABLE sources ADD CONSTRAINT sources_kind_check
    CHECK (kind IN ('FILINGS', 'EXCHANGE_DISCLOSURE', 'COMPANY_WEBSITE',
                     'BROKER', 'MANUAL', 'MARKET_DATA_VENDOR'));

-- ---------------------------------------------------------------------------

CREATE TABLE factor_scores (
    score_id         bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    instrument_id    bigint      NOT NULL REFERENCES instruments (instrument_id),
    as_of            date        NOT NULL,
    model_version    text        NOT NULL,

    -- 0-1. Percentile rank within the tracked universe on that as_of date,
    -- not a probability of anything -- see analytics/factor_score.py for why
    -- a rank is the honest thing to publish at this sample size and a
    -- calibrated probability is not.
    composite_score  numeric(5,4) NOT NULL CHECK (composite_score BETWEEN 0 AND 1),

    -- Every component that fed the composite, by name and value, so a score
    -- is never a bare number -- the same provenance discipline as
    -- metric_values.inputs, applied to a different kind of derived figure.
    factors          jsonb       NOT NULL,

    computed_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_factor_scores UNIQUE (instrument_id, as_of, model_version)
);

COMMENT ON TABLE factor_scores IS
    'A calculation, not a recommendation -- docs/01-vision-and-scope.md''s own '
    'test applies here unchanged: "can it be stated as a calculation rather '
    'than an opinion?" This table stores percentile ranks on named factors, '
    'each with its inputs attached, and nothing that reads as advice.';

CREATE INDEX idx_factor_scores_as_of ON factor_scores (as_of);

-- ii_app already holds SELECT on every table in public (migration 007's
-- blanket grant), so nothing more is needed for it to serve these two --
-- and nothing more should be granted. INSERT goes to ii_ingest alone, the
-- same asymmetry invariant 9 already enforces for every other market-data
-- table: the serving role can read what ingestion writes and cannot write
-- itself, so a bug in the read path cannot corrupt the store.
GRANT INSERT ON price_bars, factor_scores TO ii_ingest;
-- Sequence USAGE for factor_scores.score_id's IDENTITY column. price_bars
-- has no sequence (its key is the natural (instrument_id, day) pair), and
-- ii_ingest already holds blanket sequence USAGE from migration 007 --
-- this line is here for the reader, not because it changes anything.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ii_ingest;
