-- 014 · The universe is driven by filings; lifecycle only subtracts
--
-- Found by running the first real screen, which returned zero matches for
-- every criterion at every date. The metrics were correct and the facts were
-- loaded; the universe was empty.
--
-- 006's `universe_as_of` selected FROM `instrument_status_as_of`, which
-- returns no row for an instrument with no lifecycle events. The EDGAR
-- adapter ingests facts and filings but no lifecycle events, so every company
-- was outside the universe and every screen was empty.
--
-- That is a fail-closed bug, which is the better direction to fail in. It is
-- still a bad bug, because "no companies match your criteria" is a completely
-- ordinary thing for a screener to say. Nothing looked broken.
--
-- The logic was also wrong on its own terms. Absence of a delisting is not
-- absence of a company. Membership should be driven by evidence the company
-- exists and reports -- a filing -- and lifecycle events should only ever
-- REMOVE a company. Under the old shape, a source that never supplies
-- lifecycle data silently produces an empty product.

DROP FUNCTION universe_as_of(date, timestamptz);

CREATE FUNCTION universe_as_of(
    p_on     date,
    p_as_of  timestamptz DEFAULT now()
)
RETURNS TABLE (instrument_id bigint)
LANGUAGE sql STABLE AS $$
    -- Evidence of existence: we knew about a filing, covering a period that
    -- had ended, by the as-of date. This is what makes a company screenable --
    -- there is something to screen on.
    SELECT DISTINCT fl.instrument_id
    FROM   filings fl
    WHERE  fl.filed_at   <= p_as_of
      AND  fl.period_end <= p_on
      -- Delisting subtracts, and only when we had learned it AND it had taken
      -- effect. A delisting announced but not yet effective must not remove
      -- the company, and one we learn about later must not remove it
      -- retroactively from an earlier view.
      AND  NOT EXISTS (
               SELECT 1
               FROM   instrument_lifecycle l
               WHERE  l.instrument_id = fl.instrument_id
                 AND  l.event       = 'DELISTED'
                 AND  l.known_from <= p_as_of
                 AND  l.event_date <= p_on
           );
$$;

COMMENT ON FUNCTION universe_as_of(date, timestamptz) IS
    'Derived point-in-time universe. Driven by filings, because a filing is '
    'evidence a company exists and has something to screen on. Lifecycle '
    'events only subtract. NOT index membership -- that is licensed and '
    'unobtainable (Phase 3). Survivorship-bias-free: a company that later '
    'delists remains present for dates before its delisting.';

GRANT EXECUTE ON FUNCTION universe_as_of(date, timestamptz) TO ii_app, ii_ingest;
