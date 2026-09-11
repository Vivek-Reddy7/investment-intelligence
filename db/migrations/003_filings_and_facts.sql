-- 003 · Filings and financial facts
--
-- This is the heart of the system. Two things to understand:
--
-- 1. A financial fact is identified by (instrument, basis, fiscal period,
--    line item). That tuple can have MANY rows -- one per version -- ordered
--    by known_from. A restatement is a new row. Nothing is ever updated.
--
-- 2. Every fact points at the filing it came from, and every filing points at
--    the source and the run that retrieved it. That chain is what makes the
--    Phase 1 promise real: a metric can name its inputs, each input can name
--    its filing, and the filing can name its document.
--
-- Indian companies report on two bases -- standalone and consolidated -- and
-- they are genuinely different numbers. Mixing them silently is a real bug,
-- so basis is part of the fact identity rather than a display preference.

CREATE TABLE filings (
    filing_id       bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    instrument_id   bigint      NOT NULL REFERENCES instruments (instrument_id),

    filing_type     text        NOT NULL
        CHECK (filing_type IN ('QUARTERLY_RESULT', 'ANNUAL_REPORT',
                               'HALF_YEARLY_RESULT', 'RESTATEMENT')),
    period_end      date        NOT NULL,

    filed_at        timestamptz NOT NULL,   -- when the company filed it
    retrieved_at    timestamptz NOT NULL,   -- when we fetched it

    source_id       text        NOT NULL REFERENCES sources (source_id),
    run_id          bigint      REFERENCES ingestion_runs (run_id),
    source_ref      text        NOT NULL,   -- document URL or exchange reference
    content_hash    text        NOT NULL,   -- detects a re-fetch of the same document

    UNIQUE (source_id, source_ref, content_hash)
);

COMMENT ON COLUMN filings.content_hash IS
    'Lets ingestion recognise a document it has already parsed. Distinct from '
    'fact-level dedup: the same document re-fetched is one filing, but a '
    'restated document is a new filing with new facts.';

-- ---------------------------------------------------------------------------
-- Normalised line items
-- ---------------------------------------------------------------------------
-- Filings do not agree on labels. 'Revenue from operations', 'Net Sales' and
-- 'Total income from operations' are the same concept across three companies.
-- Normalising is the hard part of Phase 7/8; this table is the target vocabulary.
CREATE TABLE line_items (
    code            text        PRIMARY KEY,
    label           text        NOT NULL,
    statement       text        NOT NULL CHECK (statement IN ('PL', 'BS', 'CF')),
    is_flow         boolean     NOT NULL,   -- flow (period) vs stock (point-in-time)
    display_order   integer     NOT NULL
);

COMMENT ON COLUMN line_items.is_flow IS
    'Flow items accumulate over a period and can be summed to annual. Stock '
    'items are a balance at period end and cannot. Summing a stock item across '
    'quarters is a classic silent error.';

-- ---------------------------------------------------------------------------
-- The fact table
-- ---------------------------------------------------------------------------
CREATE TABLE financial_facts (
    fact_id         bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- Fact identity (the natural key) -------------------------------------
    instrument_id   bigint      NOT NULL REFERENCES instruments (instrument_id),
    basis           text        NOT NULL CHECK (basis IN ('STANDALONE', 'CONSOLIDATED')),
    fiscal_year     smallint    NOT NULL CHECK (fiscal_year BETWEEN 1990 AND 2100),
    period_type     text        NOT NULL
        CHECK (period_type IN ('Q1', 'Q2', 'Q3', 'Q4', 'H1', 'H2', 'ANNUAL')),
    line_item       text        NOT NULL REFERENCES line_items (code),

    -- Valid time: the period the fact describes ---------------------------
    period_start    date        NOT NULL,
    period_end      date        NOT NULL,

    -- The value -----------------------------------------------------------
    value           numeric(20, 4) NOT NULL,
    currency        char(3)     NOT NULL DEFAULT 'INR',

    -- Transaction time: when we learned it --------------------------------
    known_from      timestamptz NOT NULL,

    -- Provenance ----------------------------------------------------------
    filing_id       bigint      NOT NULL REFERENCES filings (filing_id),
    source_id       text        NOT NULL REFERENCES sources (source_id),
    run_id          bigint      REFERENCES ingestion_runs (run_id),

    CONSTRAINT period_is_ordered CHECK (period_end >= period_start),

    -- Two versions of the same fact cannot share an instant. This does NOT
    -- prevent legitimate re-versioning; it prevents an ambiguous ordering.
    -- The "don't write a version if the value is unchanged" rule is ADR 004's
    -- dedup and lives in the insert path, not here -- a UNIQUE on value would
    -- wrongly forbid a restatement that reverts to an earlier figure.
    UNIQUE (instrument_id, basis, fiscal_year, period_type, line_item, known_from)
);

-- THE index. Every as-of query is a DISTINCT ON over this ordering, so the
-- column order here is the whole performance story for the core feature.
CREATE INDEX idx_facts_asof ON financial_facts
    (instrument_id, basis, fiscal_year, period_type, line_item, known_from DESC);

-- Screening filters by line item across the universe, then picks as-of.
CREATE INDEX idx_facts_lineitem_scan ON financial_facts
    (line_item, fiscal_year, period_type, known_from DESC);

CREATE INDEX idx_facts_filing ON financial_facts (filing_id);
