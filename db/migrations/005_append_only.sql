-- 005 · Append-only enforcement
--
-- Invariant 10: no fact is ever updated or deleted. Corrections are new
-- versions.
--
-- This is the most fragile guarantee in the system. One UPDATE written by a
-- future version of us destroys history irrecoverably -- and the history IS
-- the product. So it is enforced twice, deliberately:
--
--   1. A trigger that refuses UPDATE and DELETE on every fact table. This
--      holds even for the table owner and even in a migration, so it cannot be
--      bypassed by connecting as the wrong role.
--   2. Table privileges (006_roles.sql) that do not grant UPDATE or DELETE to
--      the application role at all.
--
-- Belt and braces is the right call here because the failure is silent and
-- permanent. A dropped row does not raise an error later; it just means an
-- as-of query quietly returns the wrong answer forever.

CREATE FUNCTION refuse_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'append-only violation: % on %.% is forbidden (invariant 10)',
        TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
        USING HINT = 'Corrections are new rows with a later known_from, never edits.';
END;
$$;

COMMENT ON FUNCTION refuse_mutation() IS
    'Enforces invariant 10. Attached to every fact table. If you are reading '
    'this because a migration failed, the answer is almost never to drop the '
    'trigger -- it is that the code should be inserting a new version.';

-- Fact tables: the world as reported. Immutable.
CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON instrument_identifiers
    FOR EACH STATEMENT EXECUTE FUNCTION refuse_mutation();

CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON instrument_lifecycle
    FOR EACH STATEMENT EXECUTE FUNCTION refuse_mutation();

CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON financial_facts
    FOR EACH STATEMENT EXECUTE FUNCTION refuse_mutation();

CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON corporate_actions
    FOR EACH STATEMENT EXECUTE FUNCTION refuse_mutation();

CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON filings
    FOR EACH STATEMENT EXECUTE FUNCTION refuse_mutation();

-- ---------------------------------------------------------------------------
-- Deliberate exceptions, and why
-- ---------------------------------------------------------------------------
-- These tables are NOT append-only, and the distinction is the point:
--
--   ingestion_runs      Operational metadata about our own process, not a
--                       fact about the world. A run legitimately transitions
--                       RUNNING -> SUCCESS/FAILED, which is an update.
--
--   tracked_instruments Configuration we control (which companies to ingest).
--                       Setting removed_on is an update.
--
--   sources             Configuration. A licence note gets corrected when we
--                       re-verify terms.
--
--   line_items          Reference vocabulary.
--
-- If a table describes what a company reported, it is append-only. If it
-- describes what we decided or what our pipeline did, it is not.
