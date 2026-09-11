-- 002 · Instruments, their changing identifiers, and their lifecycle
--
-- Architecture §2.1: everything internal references an opaque instrument_id,
-- never a ticker string. Tickers on Indian exchanges get changed on renames
-- and can later be reused by a different company. A fact table keyed on the
-- string 'XYZ' will splice two unrelated companies into one history the day
-- that happens, and nothing will look wrong.
--
-- So identity lives in `instruments` (anchored on ISIN, which is stable per
-- security), and everything that changes about an instrument -- its ticker,
-- its name, whether it is still listed -- is a versioned fact.
--
-- Decision 8.1 said do not hardcode a single exchange. Hence asset_class and
-- currency as columns rather than assumptions.

CREATE TABLE instruments (
    instrument_id   bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    isin            text        NOT NULL UNIQUE CHECK (isin ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'),
    asset_class     text        NOT NULL DEFAULT 'EQUITY'
        CHECK (asset_class IN ('EQUITY', 'ETF', 'MUTUAL_FUND', 'INDEX')),
    currency        char(3)     NOT NULL DEFAULT 'INR',
    created_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE instruments IS
    'Stable identity only. Anything that can change about an instrument is a '
    'versioned fact in instrument_identifiers or instrument_lifecycle.';

-- ---------------------------------------------------------------------------
-- Versioned: what an instrument is called, and where it trades
-- ---------------------------------------------------------------------------
CREATE TABLE instrument_identifiers (
    instrument_id   bigint      NOT NULL REFERENCES instruments (instrument_id),
    exchange        text        NOT NULL CHECK (exchange IN ('NSE', 'BSE')),
    ticker          text        NOT NULL,
    company_name    text        NOT NULL,

    effective_from  date        NOT NULL,   -- valid time: when this became true
    known_from      timestamptz NOT NULL,   -- transaction time: when we learned it

    source_id       text        NOT NULL REFERENCES sources (source_id),
    run_id          bigint      REFERENCES ingestion_runs (run_id),

    PRIMARY KEY (instrument_id, exchange, effective_from, known_from)
);

-- The as-of access pattern: latest known version of each (instrument, exchange).
CREATE INDEX idx_identifiers_asof
    ON instrument_identifiers (instrument_id, exchange, known_from DESC);

-- Ticker lookup has to be as-of dated too, because tickers get reused.
CREATE INDEX idx_identifiers_ticker
    ON instrument_identifiers (exchange, ticker, known_from DESC);

-- ---------------------------------------------------------------------------
-- Versioned: listing lifecycle
-- ---------------------------------------------------------------------------
-- A delisting is a DATE, never a deletion (invariant 10). This is half of what
-- removes survivorship bias: a company we track that later fails stays in the
-- historical universe, so a screen run as of 2023 still sees it.
CREATE TABLE instrument_lifecycle (
    instrument_id   bigint      NOT NULL REFERENCES instruments (instrument_id),
    event           text        NOT NULL
        CHECK (event IN ('LISTED', 'SUSPENDED', 'RESUMED', 'DELISTED')),
    event_date      date        NOT NULL,   -- valid time
    known_from      timestamptz NOT NULL,   -- transaction time

    source_id       text        NOT NULL REFERENCES sources (source_id),
    run_id          bigint      REFERENCES ingestion_runs (run_id),

    PRIMARY KEY (instrument_id, event, event_date, known_from)
);

CREATE INDEX idx_lifecycle_asof
    ON instrument_lifecycle (instrument_id, known_from DESC, event_date);

-- ---------------------------------------------------------------------------
-- Configuration, not fact: which instruments we bother to ingest
-- ---------------------------------------------------------------------------
-- Architecture §2.13. Historical NIFTY 500 constituents sit under NSE index
-- licensing and cannot be sourced, so the current index only seeds WHICH
-- companies we track. This table is an input we control, not a queryable
-- market fact, and universe membership as of a past date is derived from our
-- own filing and lifecycle data instead (see 005_universe.sql).
CREATE TABLE tracked_instruments (
    instrument_id   bigint      PRIMARY KEY REFERENCES instruments (instrument_id),
    added_on        date        NOT NULL DEFAULT current_date,
    removed_on      date,
    reason          text        NOT NULL DEFAULT 'NIFTY500_SEED',
    CONSTRAINT removed_after_added CHECK (removed_on IS NULL OR removed_on >= added_on)
);

COMMENT ON TABLE tracked_instruments IS
    'Configuration. Which companies we ingest filings for. NOT a statement '
    'about index membership -- that is licensed and unobtainable (Phase 3).';
