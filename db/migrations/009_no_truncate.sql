-- 009 · Close the TRUNCATE hole in invariant 10
--
-- Found while writing the Phase 6 tests. The triggers in 005 fire BEFORE
-- UPDATE OR DELETE, and PostgreSQL does not route TRUNCATE through DELETE
-- triggers. So `TRUNCATE financial_facts` emptied the table with append-only
-- enforcement fully in place, and reported success.
--
-- This is the exact failure class the whole project is built against: no
-- error, no wrong-looking output, and the history is gone permanently. It was
-- only visible because the exit criterion said "enforced at the database
-- level" and that claim got tested rather than assumed.
--
-- TRUNCATE triggers must be statement-level; there are no rows to see.

CREATE TRIGGER no_truncate BEFORE TRUNCATE ON instrument_identifiers
    FOR EACH STATEMENT EXECUTE FUNCTION refuse_mutation();

CREATE TRIGGER no_truncate BEFORE TRUNCATE ON instrument_lifecycle
    FOR EACH STATEMENT EXECUTE FUNCTION refuse_mutation();

CREATE TRIGGER no_truncate BEFORE TRUNCATE ON financial_facts
    FOR EACH STATEMENT EXECUTE FUNCTION refuse_mutation();

CREATE TRIGGER no_truncate BEFORE TRUNCATE ON corporate_actions
    FOR EACH STATEMENT EXECUTE FUNCTION refuse_mutation();

CREATE TRIGGER no_truncate BEFORE TRUNCATE ON filings
    FOR EACH STATEMENT EXECUTE FUNCTION refuse_mutation();

-- Consequence for tests: cleanup cannot truncate fact tables. Tests run inside
-- a transaction that is rolled back, which is closer to how the real system
-- behaves anyway.
