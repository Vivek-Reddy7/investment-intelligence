-- 012 · Currency belongs in fact identity
--
-- Found by the first live backfill, which failed on nine of ten companies.
--
-- 003 made fact identity (instrument, basis, fiscal_year, period_type,
-- line_item) and left currency as an attribute. That was wrong, and no
-- fixture could have shown it: Infosys reports FY2019 revenue in BOTH USD and
-- INR in the same 20-F. Those are two different facts about the same period,
-- and under the old key they were one fact with two values -- so the second
-- collided with the first on (identity, known_from).
--
-- The failure was loud, which is the only reason this was cheap. Had the
-- constraint been absent instead of wrong, one currency would have silently
-- overwritten the other and every metric would have mixed units.
--
-- Note what is NOT being done: converting to a single currency on ingest.
-- That would need an exchange rate, at a date, from a source, with its own
-- provenance -- and it would destroy the as-reported figure. Both currencies
-- are stored as reported; presentation picks one.

ALTER TABLE financial_facts
    DROP CONSTRAINT financial_facts_instrument_id_basis_fiscal_year_period_type_key;

ALTER TABLE financial_facts
    ADD CONSTRAINT fact_identity_is_unique_per_instant
    UNIQUE (instrument_id, basis, fiscal_year, period_type, line_item,
            currency, known_from);

DROP INDEX idx_facts_asof;
CREATE INDEX idx_facts_asof ON financial_facts
    (instrument_id, basis, fiscal_year, period_type, line_item, currency,
     known_from DESC);

-- facts_as_of has to group by the corrected identity. Its RETURNS TABLE is
-- unchanged, but DISTINCT ON is not, so it must be replaced rather than
-- altered. Grants are re-applied because DROP removes them.
DROP FUNCTION facts_as_of(timestamptz);

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
                        f.period_type, f.line_item, f.currency)
           f.fact_id, f.instrument_id, f.basis, f.fiscal_year, f.period_type,
           f.line_item, f.period_start, f.period_end, f.value, f.currency,
           f.known_from, f.filing_id, f.source_id
    FROM   financial_facts f
    WHERE  f.known_from <= p_as_of
    ORDER  BY f.instrument_id, f.basis, f.fiscal_year, f.period_type,
              f.line_item, f.currency, f.known_from DESC;
$$;

COMMENT ON FUNCTION facts_as_of(timestamptz) IS
    'Canonical fact accessor. One row per fact identity -- which includes '
    'currency (see 012) -- being the newest version known at p_as_of.';

GRANT EXECUTE ON FUNCTION facts_as_of(timestamptz) TO ii_app, ii_ingest;
