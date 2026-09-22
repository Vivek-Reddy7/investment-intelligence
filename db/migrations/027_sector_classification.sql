-- 027 · Sector classification
--
-- Deliberately NOT local-research-only, unlike migrations 024-026. The
-- distinction is the licensing posture of the underlying data, not a
-- relaxed standard: an SEC filer's SIC code is filer metadata from
-- data.sec.gov -- the exact same domain and FAQ licence
-- (docs/03-data-sources.md, SEC_EDGAR's own row in `sources`) that already
-- covers everything financial_facts serves publicly. It carries no price
-- and depends on no third-party market-data vendor, so none of the
-- yfinance/Yahoo-terms reasoning that gates price_bars, factor_scores,
-- technical_indicators and market_cap_snapshots applies here. This table
-- may be served by the public web app once there is a route for it; the
-- restriction the other four tables carry is not inherited by default.

CREATE TABLE sector_classifications (
    instrument_id    bigint      NOT NULL REFERENCES instruments (instrument_id),
    sic_code         text        NOT NULL,
    sic_description  text        NOT NULL,

    -- A coarse bucket over SIC's ~1,000 codes -- see
    -- analytics/classification.py's SIC_SECTOR_MAP for the ranges. "Sector"
    -- here means the classic broad grouping (Technology, Financials,
    -- Healthcare, ...), not GICS or any licensed taxonomy -- SIC itself is
    -- a US government classification, public by construction.
    sector           text        NOT NULL,

    source_id        text        NOT NULL REFERENCES sources (source_id),
    verified_on      date        NOT NULL,
    computed_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_sector_classifications UNIQUE (instrument_id)
);

COMMENT ON TABLE sector_classifications IS
    'SIC code and a coarse sector bucket, from SEC EDGAR filer metadata. '
    'One row per instrument -- SIC is treated as current-state company '
    'metadata, not a bitemporal fact, since a reclassification is rare '
    'and this project does not yet need to answer "what sector was this '
    'company classified as of a past date".';

GRANT SELECT ON sector_classifications TO ii_app, ii_ingest;
GRANT INSERT, UPDATE ON sector_classifications TO ii_ingest;
