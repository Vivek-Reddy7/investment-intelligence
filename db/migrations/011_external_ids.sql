-- 011 · External identifiers, and ISIN becomes optional
--
-- Discovered by contact with a real source, which is what stage one is for.
--
-- 002_instruments.sql anchored identity on ISIN, on the reasoning that ISIN is
-- stable per security in India. That is true, and it is not sufficient. The
-- first working source is SEC EDGAR, where the natural key is a CIK, and an
-- Indian ADR issuer has both: an Indian ISIN for its NSE listing and a US CIK
-- for its SEC filings. Neither is derivable from the other.
--
-- Forcing ISIN to be present would have meant inventing ISINs for companies
-- whose SEC data we can read but whose ISIN we have not verified -- which is
-- precisely the identity bug 002 was written to prevent, arriving through the
-- front door.
--
-- So: an instrument carries zero or more external identifiers in named
-- schemes, and must have at least one. ISIN stays a first-class column because
-- it is the anchor for the Indian listing, but it may be null until verified.

CREATE TABLE instrument_external_ids (
    instrument_id   bigint      NOT NULL REFERENCES instruments (instrument_id),
    scheme          text        NOT NULL
        CHECK (scheme IN ('ISIN', 'SEC_CIK', 'NSE_SYMBOL', 'BSE_CODE', 'US_TICKER')),
    value           text        NOT NULL CHECK (length(btrim(value)) > 0),

    -- Where this mapping came from. An identifier asserted by nobody is how
    -- two companies' histories get spliced together.
    source_id       text        NOT NULL REFERENCES sources (source_id),
    verified_on     date        NOT NULL DEFAULT current_date,

    PRIMARY KEY (scheme, value),
    UNIQUE (instrument_id, scheme)
);

COMMENT ON TABLE instrument_external_ids IS
    'One row per (scheme, value). The PK enforces that an external id maps to '
    'at most one instrument -- the property whose absence lets a reused ticker '
    'merge two companies. UNIQUE (instrument_id, scheme) enforces the other '
    'direction: one CIK per instrument, not two.';

ALTER TABLE instruments ALTER COLUMN isin DROP NOT NULL;

-- An instrument with no identifier at all is unreachable and meaningless.
-- Checked by trigger rather than a constraint, because the identifier lives in
-- another table and CHECK cannot see it.
CREATE FUNCTION instrument_must_be_identifiable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT isin FROM instruments WHERE instrument_id = OLD.instrument_id) IS NULL
       AND NOT EXISTS (SELECT 1 FROM instrument_external_ids
                       WHERE instrument_id = OLD.instrument_id)
    THEN
        RAISE EXCEPTION
            'instrument % would have no identifier left', OLD.instrument_id;
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER instrument_stays_identifiable
    AFTER DELETE ON instrument_external_ids
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION instrument_must_be_identifiable();

GRANT SELECT ON instrument_external_ids TO ii_app;
GRANT SELECT, INSERT, UPDATE ON instrument_external_ids TO ii_ingest;
