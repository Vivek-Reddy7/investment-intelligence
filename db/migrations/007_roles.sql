-- 007 · Roles and privileges
--
-- The second half of invariant 10's enforcement, and simultaneously a Phase 15
-- security control. ADR 005: the application role is granted INSERT and SELECT
-- on fact tables and NOT UPDATE or DELETE.
--
-- The trigger in 005 already refuses mutation. Privileges matter anyway,
-- because they mean a compromised application credential cannot even attempt
-- to rewrite history, and because a future maintainer who drops the trigger
-- still hits a permission denial.
--
-- Roles are cluster-wide in Postgres, so these use IF NOT EXISTS guards to
-- stay idempotent across databases and re-runs.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ii_app') THEN
        CREATE ROLE ii_app NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ii_ingest') THEN
        CREATE ROLE ii_ingest NOLOGIN;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- ii_app  — what serves the website. Reads only.
-- ---------------------------------------------------------------------------
-- Architecture invariant 9: no serving path triggers an external fetch. This
-- goes further: no serving path writes anything at all. The read API cannot
-- corrupt the store even if it has a bug.
GRANT USAGE ON SCHEMA public TO ii_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ii_app;
GRANT EXECUTE ON FUNCTION facts_as_of(timestamptz)                  TO ii_app;
GRANT EXECUTE ON FUNCTION instrument_status_as_of(date, timestamptz) TO ii_app;
GRANT EXECUTE ON FUNCTION universe_as_of(date, timestamptz)          TO ii_app;

-- ---------------------------------------------------------------------------
-- ii_ingest  — what writes facts. Append only.
-- ---------------------------------------------------------------------------
GRANT USAGE ON SCHEMA public TO ii_ingest;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ii_ingest;

-- Facts: insert, never modify.
GRANT INSERT ON instrument_identifiers, instrument_lifecycle,
                financial_facts, corporate_actions, filings TO ii_ingest;

-- Operational and configuration tables: these are legitimately mutable
-- (see the exceptions note in 005_append_only.sql).
GRANT INSERT, UPDATE ON ingestion_runs      TO ii_ingest;
GRANT INSERT, UPDATE ON tracked_instruments TO ii_ingest;
GRANT INSERT, UPDATE ON sources             TO ii_ingest;
GRANT INSERT, UPDATE ON line_items          TO ii_ingest;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ii_ingest;

-- Note what is deliberately absent: no DELETE anywhere, for either role.
-- Nothing in this system has a legitimate reason to delete a row.
