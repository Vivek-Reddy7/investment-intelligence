-- 006 · The as-of accessors
--
-- Invariant 12: every query is as-of dated, defaulting to now. There is no
-- code path that reads "the value" of a fact without an implied as-of date.
--
-- ADR 005 chose plain SQL over a framework precisely so the as-of predicate
-- stays visible. These functions are the one place it is written. Anything
-- that reads facts goes through them, so if the predicate is wrong it is
-- wrong in exactly one place rather than subtly wrong in twelve.
--
-- The whole mechanism is DISTINCT ON over (fact identity) ORDER BY
-- known_from DESC, filtered to known_from <= as_of. That is it. Everything
-- else in this project exists to make that query mean something.

-- ---------------------------------------------------------------------------
-- facts_as_of: the latest version of every financial fact, as we knew it
-- ---------------------------------------------------------------------------
CREATE FUNCTION facts_as_of(p_as_of timestamptz DEFAULT now())
RETURNS TABLE (
    fact_id       bigint,
    instrument_id bigint,
    basis         text,
    fiscal_year   smallint,
    period_type   text,
    line_item     text,
    period_start  date,
    period_end    date,
    value         numeric,
    currency      char(3),
    known_from    timestamptz,
    filing_id     bigint,
    source_id     text
)
LANGUAGE sql STABLE AS $$
    SELECT DISTINCT ON (f.instrument_id, f.basis, f.fiscal_year,
                        f.period_type, f.line_item)
           f.fact_id, f.instrument_id, f.basis, f.fiscal_year, f.period_type,
           f.line_item, f.period_start, f.period_end, f.value, f.currency,
           f.known_from, f.filing_id, f.source_id
    FROM   financial_facts f
    WHERE  f.known_from <= p_as_of
    ORDER  BY f.instrument_id, f.basis, f.fiscal_year, f.period_type,
              f.line_item, f.known_from DESC;
$$;

COMMENT ON FUNCTION facts_as_of(timestamptz) IS
    'Canonical fact accessor. Returns one row per fact identity: the newest '
    'version whose known_from is at or before p_as_of. Passing a past '
    'timestamp reproduces exactly what we believed then, including figures '
    'later restated.';

-- ---------------------------------------------------------------------------
-- instrument_status_as_of: listed or not, as we knew it
-- ---------------------------------------------------------------------------
-- Two time axes both matter here. We only consider lifecycle events we had
-- LEARNED by p_as_of (known_from), and among those only ones that had taken
-- EFFECT by p_on (event_date). A delisting announced but not yet effective
-- must not remove a company from the universe.
CREATE FUNCTION instrument_status_as_of(
    p_on     date,
    p_as_of  timestamptz DEFAULT now()
)
RETURNS TABLE (instrument_id bigint, status text, since date)
LANGUAGE sql STABLE AS $$
    SELECT DISTINCT ON (l.instrument_id)
           l.instrument_id,
           l.event AS status,
           l.event_date AS since
    FROM   instrument_lifecycle l
    WHERE  l.known_from <= p_as_of
      AND  l.event_date <= p_on
    ORDER  BY l.instrument_id, l.event_date DESC, l.known_from DESC;
$$;

-- ---------------------------------------------------------------------------
-- universe_as_of: who was investable, derived from our own data
-- ---------------------------------------------------------------------------
-- Architecture §2.13. Historical NIFTY 500 constituents are licensed and
-- unobtainable, so membership is derived rather than ingested: an instrument
-- is in the universe as of a date if we had learned it was listed and not
-- delisted by then, and it had filed at least one statement we knew about.
--
-- This is weaker than true index reconstruction and the UI must say so. It is
-- however free of the survivorship bias that matters: a tracked company that
-- later delists still appears in the historical universe, because the
-- delisting is a date and not a deletion.
CREATE FUNCTION universe_as_of(
    p_on     date,
    p_as_of  timestamptz DEFAULT now()
)
RETURNS TABLE (instrument_id bigint)
LANGUAGE sql STABLE AS $$
    SELECT s.instrument_id
    FROM   instrument_status_as_of(p_on, p_as_of) s
    WHERE  s.status <> 'DELISTED'
      AND  EXISTS (
               SELECT 1
               FROM   filings fl
               WHERE  fl.instrument_id = s.instrument_id
                 AND  fl.filed_at     <= p_as_of
                 AND  fl.period_end   <= p_on
           );
$$;

COMMENT ON FUNCTION universe_as_of(date, timestamptz) IS
    'Derived point-in-time universe. Not index membership -- that is licensed '
    'and unobtainable (Phase 3). Survivorship-bias-free: delisted companies '
    'remain present for dates before their delisting.';
