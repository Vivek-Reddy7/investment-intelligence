-- Neon one-time setup: the serving role.
--
-- Run this ONCE against a fresh Neon project, with psql, using the DIRECT
-- connection string (not the -pooler one) and the owner role:
--
--   psql "$NEON_DIRECT_URL" -v ON_ERROR_STOP=1 \
--        -v web_password="$(openssl rand -base64 24)" \
--        -f scripts/neon_setup.sql
--
-- Run `migrate` FIRST -- via the ingest workflow, or locally against the Neon
-- URL. The grants below reference tables the migrations create, and ii_app
-- itself is created by migration 007.
--
-- WHY THIS IS NOT A MIGRATION
-- A role needs a password. A password in a migration is a password in git,
-- forever, in every clone. Migration 007 creates the privilege-bearing roles
-- (ii_app, ii_ingest, ii_alerts) because those are LOGIN-less and carry no
-- secret. Only the login role that Vercel uses needs one, so only that one
-- lives here, taking its password from a psql variable rather than the file.
--
-- WHY A SEPARATE ROLE AT ALL
-- Invariants 9 and 10 say the serving path cannot rewrite history. That is
-- enforced by privileges, not by the application being careful -- which only
-- holds if the deployed credential is genuinely different from the ingestion
-- one. Two connection strings with different rights, per docs/12-deployment.md.

\if :{?web_password}
\else
  \echo 'ERROR: pass -v web_password=... (e.g. from `openssl rand -base64 24`)'
  \quit
\endif

-- Idempotent: roles are cluster-wide, so a re-run must not fail the script.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ii_web') THEN
        CREATE ROLE ii_web LOGIN;
    END IF;
END
$$;

ALTER ROLE ii_web PASSWORD :'web_password';

-- ii_web inherits ii_app: SELECT on market data, full CRUD on the user schema,
-- and no ability to mutate history. It gets nothing directly of its own, so
-- there is one place to look when asking what the website can do.
GRANT ii_app TO ii_web;

\echo ''
\echo 'ii_web ready. Build DATABASE_URL for Vercel from these parts:'
\echo '  scheme    postgresql'
\echo '  user      ii_web, with the password you just generated'
\echo '  host      the POOLED host -- the one containing "-pooler"'
\echo '  query     sslmode=require'
\echo ''
\echo 'Assembled by hand on purpose: this script does not print a ready-made'
\echo 'URL, because a connection string with a password in it is the exact'
\echo 'thing that ends up pasted into a commit. The secret scanner in'
\echo 'tests/test_security.py rejects that shape, including in examples.'
\echo ''
\echo 'The ingestion job uses the DIRECT host and a write-capable role instead.'
\echo 'See docs/12-deployment.md section 3 for why those must not be swapped.'
