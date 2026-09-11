-- 021 · Rate limiting
--
-- Two vectors, both real, both specific to what this platform does.
--
-- 1. LOGIN EMAIL FLOODING. Phase 13 left `begin_login` unlimited: anyone
--    could request unbounded magic links for any address. That is an
--    email-bombing tool aimed at a third party, and it burns whatever send
--    quota we have. Noted as a Phase 15 item at the time.
--
-- 2. EXPENSIVE HISTORICAL SCREENS. This one is ours alone. A live screen
--    reads the materialised `metric_values`; a historical screen calls
--    `metrics_as_of(date)`, which recomputes the entire pivot over every
--    versioned fact. It is unauthenticated, it is the headline feature, and
--    the as-of date is a free parameter -- so an attacker gets an arbitrary
--    number of distinct, uncacheable, full-table computations for the price
--    of a query string. On a free tier with 100 compute-hours a month that is
--    a cheap way to take the site down, and nothing about it looks like an
--    attack in a log.
--
-- Fixed windows rather than a token bucket. A bucket smooths bursts, which is
-- what you want for an API a customer pays for; here a burst IS the abuse, and
-- a fixed window is easier to reason about and to explain in a 429.
--
-- In Postgres rather than Redis because there is no Redis on the free tier,
-- and a counter that survives a restart is better than one that does not.

CREATE TABLE app.rate_limits (
    action      text        NOT NULL,
    -- The thing being limited: an email address, an IP, a user id. Hashed by
    -- the caller where it is personal data, so this table is not a log of who
    -- visited from where.
    subject     text        NOT NULL,
    window_start timestamptz NOT NULL,
    attempts    integer     NOT NULL DEFAULT 1 CHECK (attempts > 0),
    PRIMARY KEY (action, subject, window_start)
);

CREATE INDEX idx_rate_limits_sweep ON app.rate_limits (window_start);

COMMENT ON TABLE app.rate_limits IS
    'Fixed-window counters. Not row-level-secured: the evaluator must count '
    'attempts across all users, and a subject is stored hashed where it is '
    'personal so the table is not a visitor log.';

-- Deliberately readable and writable by the serving role, and deliberately
-- NOT under RLS -- a rate limiter that could only see its own user''s attempts
-- would not be a rate limiter.
GRANT SELECT, INSERT, UPDATE, DELETE ON app.rate_limits TO ii_app;

CREATE FUNCTION app.sweep_rate_limits(p_older_than interval DEFAULT '1 day')
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE n integer;
BEGIN
    DELETE FROM app.rate_limits WHERE window_start < now() - p_older_than;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$;

GRANT EXECUTE ON FUNCTION app.sweep_rate_limits(interval) TO ii_app;
