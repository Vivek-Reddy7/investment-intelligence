-- 026 · Fix missing grants on price_bars, factor_scores, technical_indicators
--
-- Two real gaps, one root cause: migrations 024 and 025 broke a convention
-- every earlier table-adding migration followed without exception --
-- 011, 013, 015, 017, 018 all explicitly re-grant SELECT to ii_app and
-- ii_ingest on the table they add. Migration 007's blanket
-- "GRANT SELECT ON ALL TABLES IN SCHEMA public" only ever covered tables
-- that existed at the moment it ran; every table since has needed its own
-- explicit re-grant, and 024/025 omitted it for all three new tables.
--
-- Consequence: neither ii_app nor ii_ingest could SELECT price_bars,
-- factor_scores or technical_indicators at all. ii_ingest -- the role that
-- actually runs `prices`, `factors` and `technicals` for real -- could not
-- even read back what it had just written.
--
-- Separately, factor_scores was missing UPDATE for ii_ingest. Its writer
-- uses INSERT ... ON CONFLICT DO UPDATE, which Postgres requires BOTH
-- INSERT and UPDATE privilege for; migration 024 granted only INSERT.
-- technical_indicators (025) already grants both together and does not
-- have this half of the bug.
--
-- Neither gap showed up in this session's own testing, and that is the
-- real lesson, not the specific grants. Every manual verification ran as
-- the database owner, a superuser, which bypasses privilege checks
-- entirely -- the exact blind spot tests/conftest.py's app_role helper
-- exists to guard against for RLS, now caught in a different corner by
-- deliberately testing as ii_ingest instead of trusting a run that merely
-- happened to work. test_no_role_holds_a_privilege_nobody_decided_on could
-- not have caught this either: it enumerates grants a role HAS to check
-- none are excessive, which is structurally blind to a grant a role is
-- simply missing.

GRANT SELECT ON price_bars, factor_scores, technical_indicators
    TO ii_app, ii_ingest;

GRANT UPDATE ON factor_scores TO ii_ingest;
