-- 020 · The user domain
--
-- Two structural decisions, both about enforcement rather than convention.
--
-- 1. A SEPARATE SCHEMA. User data lives in `app`, market data stays in
--    `public`. Invariant 8 says market data never reads from the user domain,
--    and a schema boundary lets that be a privilege rather than a promise:
--    `ii_ingest` is granted nothing on `app`, so an ingestion job physically
--    cannot join against a watchlist. There is also no foreign key from
--    `public` into `app`, in either direction of intent.
--
-- 2. ROW LEVEL SECURITY. One user must not read another's watchlist,
--    portfolio, or saved screens. Enforcing that in application queries means
--    every future query is one forgotten WHERE clause away from a data leak,
--    and the leak is silent -- the page renders, with someone else's holdings.
--    So it is enforced by the database: RLS policies keyed on a session
--    variable the web app sets per request. `ii_app` is not a superuser and
--    does not have BYPASSRLS, so it cannot opt out.
--
-- Note what is deliberately absent: any password column. Authentication is by
-- emailed magic link (see docs/13-accounts.md). We never store, hash, compare
-- or transport a password, which removes the entire category of failure.
--
-- Also absent: any monetary valuation of a portfolio. Prices are behind the
-- private path (Phase 3), so a portfolio here is bookkeeping the user typed
-- in. No P&L, no market value, no custody -- Phase 1 §3.

CREATE SCHEMA app;

-- ---------------------------------------------------------------------------
-- Identity
-- ---------------------------------------------------------------------------
CREATE TABLE app.users (
    user_id      uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    email        text        NOT NULL CHECK (email = lower(btrim(email))
                                             AND position('@' in email) > 1),
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz
);

-- Case-insensitive uniqueness without depending on the citext extension,
-- which is not guaranteed present on a managed free tier.
CREATE UNIQUE INDEX idx_users_email ON app.users (email);

COMMENT ON COLUMN app.users.email IS
    'Stored already-normalised (lowercase, trimmed) and the CHECK enforces it, '
    'so two rows cannot differ only by case and become two accounts for one '
    'person.';

-- ---------------------------------------------------------------------------
-- Sessions and sign-in tokens
-- ---------------------------------------------------------------------------
-- Only hashes are stored. A leaked database must not yield usable sessions,
-- and there is no legitimate reason for us to be able to reconstruct a token.
CREATE TABLE app.sessions (
    token_hash  bytea       PRIMARY KEY,
    user_id     uuid        NOT NULL REFERENCES app.users (user_id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    user_agent  text,
    CONSTRAINT sessions_expire_in_the_future CHECK (expires_at > created_at)
);

CREATE INDEX idx_sessions_user ON app.sessions (user_id);
CREATE INDEX idx_sessions_expiry ON app.sessions (expires_at);

CREATE TABLE app.login_tokens (
    token_hash  bytea       PRIMARY KEY,
    email       text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    consumed_at timestamptz,
    CONSTRAINT login_tokens_expire_in_the_future CHECK (expires_at > created_at)
);

COMMENT ON TABLE app.login_tokens IS
    'Single-use magic-link tokens, stored as hashes. `consumed_at` makes reuse '
    'detectable: a token presented twice means either a double-click or a '
    'replay, and both should fail closed.';

-- ---------------------------------------------------------------------------
-- Personalisation
-- ---------------------------------------------------------------------------
CREATE TABLE app.watchlists (
    watchlist_id uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid        NOT NULL REFERENCES app.users (user_id) ON DELETE CASCADE,
    name         text        NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 80),
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, name)
);

-- `instrument_id` is a plain bigint, NOT a foreign key into public.instruments.
-- That is deliberate and is the only place in the project where a missing FK
-- is the right call: a foreign key here would make market data and user data
-- mutually dependent, so dropping and rebuilding the market schema (which the
-- test harness does routinely, and a future re-model might) would cascade into
-- user rows. Referential integrity is checked in the application, which is the
-- cost of keeping the two domains genuinely separable.
CREATE TABLE app.watchlist_items (
    watchlist_id uuid        NOT NULL REFERENCES app.watchlists (watchlist_id) ON DELETE CASCADE,
    instrument_id bigint     NOT NULL,
    added_at     timestamptz NOT NULL DEFAULT now(),
    note         text,
    PRIMARY KEY (watchlist_id, instrument_id)
);

CREATE TABLE app.saved_screens (
    screen_id   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid        NOT NULL REFERENCES app.users (user_id) ON DELETE CASCADE,
    name        text        NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 80),
    -- The criteria as the API accepts them. jsonb rather than columns because
    -- a screen is a variable-length list of (metric, operator, threshold), and
    -- validation happens where the screen is run, against the metrics table.
    criteria    jsonb       NOT NULL CHECK (jsonb_typeof(criteria) = 'array'
                                            AND jsonb_array_length(criteria) BETWEEN 1 AND 8),
    fiscal_year smallint    NOT NULL,
    currency    char(3)     NOT NULL,
    -- A saved screen may pin an as-of date. Null means "always current",
    -- which is a different saved object from "as it looked in June 2020".
    as_of       timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, name)
);

CREATE TABLE app.portfolio_entries (
    entry_id     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid        NOT NULL REFERENCES app.users (user_id) ON DELETE CASCADE,
    instrument_id bigint     NOT NULL,
    quantity     numeric(20, 4) NOT NULL CHECK (quantity > 0),
    cost_basis   numeric(20, 4) CHECK (cost_basis >= 0),
    currency     char(3)     NOT NULL DEFAULT 'INR',
    acquired_on  date,
    note         text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE app.portfolio_entries IS
    'Bookkeeping the user typed in. No broker, no custody, no KYC, and no '
    'market value -- prices are behind the private path, so this cannot and '
    'does not compute a profit or loss.';

-- ---------------------------------------------------------------------------
-- Alerts
-- ---------------------------------------------------------------------------
-- On conditions the USER defines. Not signals: under SEBI's Research Analyst
-- regulations the distinction is whether the platform or the reader supplies
-- the judgement, and here the reader does (Phase 1 §3).
CREATE TABLE app.alert_rules (
    rule_id      uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid        NOT NULL REFERENCES app.users (user_id) ON DELETE CASCADE,
    -- Null means "any company in the universe", which is how a screen becomes
    -- an alert: tell me when anything starts meeting this condition.
    instrument_id bigint,
    metric_code  text        NOT NULL,
    operator     text        NOT NULL CHECK (operator IN ('gt', 'gte', 'lt', 'lte')),
    threshold    numeric     NOT NULL,
    fiscal_year  smallint,
    currency     char(3)     NOT NULL DEFAULT 'INR',
    enabled      boolean     NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_alert_rules_enabled ON app.alert_rules (enabled) WHERE enabled;

-- Remembering whether a rule was already firing is what turns "the condition
-- is true" into "the condition just became true". Without it every cycle
-- re-delivers every satisfied alert, and a user who set one threshold gets a
-- message every day until they unsubscribe from all of them.
CREATE TABLE app.alert_state (
    rule_id       uuid       NOT NULL REFERENCES app.alert_rules (rule_id) ON DELETE CASCADE,
    instrument_id bigint     NOT NULL,
    was_triggered boolean    NOT NULL,
    last_value    numeric,
    evaluated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (rule_id, instrument_id)
);

CREATE TABLE app.alert_deliveries (
    delivery_id   bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rule_id       uuid        NOT NULL REFERENCES app.alert_rules (rule_id) ON DELETE CASCADE,
    user_id       uuid        NOT NULL REFERENCES app.users (user_id) ON DELETE CASCADE,
    instrument_id bigint      NOT NULL,
    observed      numeric     NOT NULL,
    threshold     numeric     NOT NULL,
    fired_at      timestamptz NOT NULL DEFAULT now(),
    channel       text        NOT NULL DEFAULT 'EMAIL',
    delivered_at  timestamptz,
    error         text
);

CREATE INDEX idx_deliveries_user ON app.alert_deliveries (user_id, fired_at DESC);
CREATE INDEX idx_deliveries_pending ON app.alert_deliveries (fired_at)
    WHERE delivered_at IS NULL;

-- ---------------------------------------------------------------------------
-- Row level security
-- ---------------------------------------------------------------------------
-- The property that matters. Every user-owned table is readable and writable
-- only by its owner, enforced below the application. A future query that
-- forgets its WHERE clause returns nothing rather than everything.
--
-- `app.current_user_id` is set per request by the web app. When it is unset,
-- `current_setting(..., true)` returns NULL and every policy evaluates false --
-- so an unauthenticated request sees no rows at all, which is the correct
-- default. Failing closed matters more here than anywhere else in the project.

CREATE FUNCTION app.current_user_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('app.current_user_id', true), '')::uuid;
$$;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['users', 'watchlists', 'saved_screens',
                             'portfolio_entries', 'alert_rules',
                             'alert_deliveries']
    LOOP
        EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE app.%I FORCE ROW LEVEL SECURITY', t);
    END LOOP;
END $$;

CREATE POLICY own_row ON app.users
    USING (user_id = app.current_user_id())
    WITH CHECK (user_id = app.current_user_id());

CREATE POLICY own_rows ON app.watchlists
    USING (user_id = app.current_user_id())
    WITH CHECK (user_id = app.current_user_id());

CREATE POLICY own_rows ON app.saved_screens
    USING (user_id = app.current_user_id())
    WITH CHECK (user_id = app.current_user_id());

CREATE POLICY own_rows ON app.portfolio_entries
    USING (user_id = app.current_user_id())
    WITH CHECK (user_id = app.current_user_id());

CREATE POLICY own_rows ON app.alert_rules
    USING (user_id = app.current_user_id())
    WITH CHECK (user_id = app.current_user_id());

CREATE POLICY own_rows ON app.alert_deliveries
    USING (user_id = app.current_user_id())
    WITH CHECK (user_id = app.current_user_id());

-- Watchlist items are owned transitively. Checked through the parent rather
-- than by duplicating user_id, so the two cannot disagree.
ALTER TABLE app.watchlist_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.watchlist_items FORCE ROW LEVEL SECURITY;
CREATE POLICY own_rows ON app.watchlist_items
    USING (EXISTS (SELECT 1 FROM app.watchlists w
                   WHERE w.watchlist_id = watchlist_items.watchlist_id
                     AND w.user_id = app.current_user_id()))
    WITH CHECK (EXISTS (SELECT 1 FROM app.watchlists w
                        WHERE w.watchlist_id = watchlist_items.watchlist_id
                          AND w.user_id = app.current_user_id()));

-- ---------------------------------------------------------------------------
-- Privileges
-- ---------------------------------------------------------------------------
-- ii_app serves the website and now writes user data -- which is mutable, and
-- nothing like the append-only fact tables. RLS is what keeps it honest.
GRANT USAGE ON SCHEMA app TO ii_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO ii_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app TO ii_app;
GRANT EXECUTE ON FUNCTION app.current_user_id() TO ii_app;

-- Invariant 8, as a privilege. The ingestion role is granted NOTHING on app,
-- so an ingestion job cannot read a watchlist even by accident. Alert
-- evaluation therefore needs its own role rather than reusing ii_ingest.
-- Guarded, because roles are cluster-wide rather than per-database: a
-- re-applied migration, or a second database in the same cluster, hits an
-- existing role. Migration 007 got this right and this one did not, until the
-- test harness -- which rebuilds the schema around every committing test --
-- failed on it immediately.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ii_alerts') THEN
        CREATE ROLE ii_alerts NOLOGIN;
    END IF;
END $$;
GRANT USAGE ON SCHEMA app, public TO ii_alerts;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ii_alerts;
GRANT SELECT, INSERT, UPDATE ON app.alert_rules, app.alert_state,
      app.alert_deliveries TO ii_alerts;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app TO ii_alerts;
GRANT EXECUTE ON FUNCTION app.current_user_id() TO ii_alerts;

-- The evaluator legitimately works across all users, so it is exempt from RLS
-- on exactly the three tables it needs and nothing else. Stated explicitly
-- rather than achieved by making it a superuser.
ALTER TABLE app.alert_rules FORCE ROW LEVEL SECURITY;
CREATE POLICY evaluator_reads_all ON app.alert_rules
    FOR SELECT TO ii_alerts USING (true);
CREATE POLICY evaluator_writes_deliveries ON app.alert_deliveries
    FOR INSERT TO ii_alerts WITH CHECK (true);
CREATE POLICY evaluator_reads_deliveries ON app.alert_deliveries
    FOR SELECT TO ii_alerts USING (true);
