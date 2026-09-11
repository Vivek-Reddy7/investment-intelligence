"""Phase 13 exit criteria, as tests.

    A user can sign in, save a screen, and receive an alert.
    Market data still has no dependency on the user domain (invariant 8).

The isolation tests are the ones that matter. A leak between users is silent:
the page renders, with someone else's holdings on it. So they are tested
against the database's own enforcement rather than against our queries — the
queries in `store.py` deliberately contain no `user_id` predicate at all, so
if RLS were not working these tests would return everyone's rows.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest

from conftest import add_fact, add_filing, app_role, utc
from investment_intelligence.accounts import alerts, store
from investment_intelligence.accounts.auth import (
    AuthError,
    LOGIN_TOKEN_TTL,
    authenticate,
    begin_login,
    complete_login,
    normalise_email,
    purge_expired,
    sign_out,
)

FY = 2025


@pytest.fixture
def alice(conn):
    link = begin_login(conn, "Alice@Example.COM ")
    return complete_login(conn, link.token)


@pytest.fixture
def bob(conn):
    link = begin_login(conn, "bob@example.com")
    return complete_login(conn, link.token)


@pytest.fixture
def company(conn, source):
    """One instrument with a screenable ROE of 20%."""
    with conn.cursor() as cur:
        cur.execute("INSERT INTO instruments (isin) VALUES ('INE000ACCT01') "
                    "RETURNING instrument_id")
        instrument_id = cur.fetchone()[0]
        cur.execute("INSERT INTO tracked_instruments (instrument_id) VALUES (%s)",
                    (instrument_id,))
    filing = add_filing(conn, instrument_id, source, period_end=date(FY, 3, 31),
                        filed_at=utc(FY, 6, 1), ref="acct")
    for item, value in (("REVENUE", 1000), ("NET_PROFIT", 100),
                        ("TOTAL_ASSETS", 1000), ("TOTAL_EQUITY", 500)):
        add_fact(conn, instrument_id, source, filing, line_item=item,
                 value=Decimal(value), fiscal_year=FY, period_type="ANNUAL",
                 known_from=utc(FY, 6, 1),
                 period_start=date(FY - 1, 4, 1), period_end=date(FY, 3, 31))
    with conn.cursor() as cur:
        cur.execute("SELECT refresh_metric_values()")
    return instrument_id


# ---------------------------------------------------------------------------
# Sign-in
# ---------------------------------------------------------------------------

def test_a_user_can_sign_in(conn):
    link = begin_login(conn, "new@example.com")
    session = complete_login(conn, link.token)
    assert authenticate(conn, session.token) == session.user_id


def test_no_password_is_stored_anywhere(conn, alice):
    """The schema has no password column, by design. Asserted rather than
    assumed, because adding one later would reintroduce a whole category of
    failure and should have to fail a test first."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name FROM information_schema.columns
            WHERE  table_schema = 'app'
              AND  (column_name ILIKE '%password%' OR column_name ILIKE '%passwd%')
            """)
        assert cur.fetchall() == []


def test_only_hashes_are_stored(conn):
    """A leaked database must not yield usable credentials."""
    link = begin_login(conn, "hash@example.com")
    session = complete_login(conn, link.token)
    with conn.cursor() as cur:
        cur.execute("SELECT token_hash FROM app.login_tokens")
        stored = [bytes(r[0]) for r in cur.fetchall()]
        cur.execute("SELECT token_hash FROM app.sessions")
        stored += [bytes(r[0]) for r in cur.fetchall()]
    for value in stored:
        assert link.token.encode() not in value
        assert session.token.encode() not in value
        assert len(value) == 32, "expected a sha-256 digest"


def test_email_is_normalised_so_one_person_gets_one_account(conn):
    """Without this, Alice@Example.com and alice@example.com become two
    accounts and the second one silently has none of her data."""
    first = complete_login(conn, begin_login(conn, "Mixed@Case.com").token)
    second = complete_login(conn, begin_login(conn, "  mixed@case.COM  ").token)
    assert first.user_id == second.user_id


@pytest.mark.parametrize("bad", ["", "   ", "nope", "@example.com", "a@b@c", "a@"])
def test_an_unusable_email_is_refused(conn, bad):
    with pytest.raises(AuthError):
        begin_login(conn, bad)


def test_a_login_token_is_single_use(conn):
    """A second presentation is either a double-click or a replay. Both fail."""
    link = begin_login(conn, "once@example.com")
    complete_login(conn, link.token)
    with pytest.raises(AuthError):
        complete_login(conn, link.token)


def test_an_expired_login_token_is_refused(conn):
    link = begin_login(conn, "slow@example.com")
    with conn.cursor() as cur:
        # created_at moves too: the CHECK (expires_at > created_at) is correct
        # and it is the test that was lazy.
        cur.execute("UPDATE app.login_tokens SET created_at = now() - interval '1 hour', "
                    "expires_at = now() - interval '1 minute'")
    with pytest.raises(AuthError):
        complete_login(conn, link.token)


def test_an_unknown_token_fails_the_same_way_as_an_expired_one(conn):
    """Failures must be indistinguishable, or the sign-in form becomes an
    account-enumeration oracle."""
    link = begin_login(conn, "real@example.com")
    with conn.cursor() as cur:
        cur.execute("UPDATE app.login_tokens SET created_at = now() - interval '1 hour', "
                    "expires_at = now() - interval '1 minute'")

    with pytest.raises(AuthError) as expired:
        complete_login(conn, link.token)
    with pytest.raises(AuthError) as unknown:
        complete_login(conn, "a-token-that-was-never-issued")
    assert str(expired.value) == str(unknown.value)


def test_beginning_a_login_reveals_nothing_about_the_account(conn, alice):
    """Same response shape for an existing and a non-existent address, and no
    account is created until a token is actually consumed."""
    existing = begin_login(conn, "alice@example.com")
    stranger = begin_login(conn, "nobody@example.com")
    assert existing.expires_at.date() == stranger.expires_at.date()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM app.users WHERE email = 'nobody@example.com'")
        assert cur.fetchone()[0] == 0


def test_an_expired_session_does_not_authenticate(conn, alice):
    with conn.cursor() as cur:
        cur.execute("UPDATE app.sessions SET created_at = now() - interval '1 hour', "
                    "expires_at = now() - interval '1 second'")
    assert authenticate(conn, alice.token) is None


def test_signing_out_invalidates_the_session(conn, alice):
    sign_out(conn, alice.token)
    assert authenticate(conn, alice.token) is None


def test_an_absent_token_is_simply_not_signed_in(conn):
    assert authenticate(conn, None) is None
    assert authenticate(conn, "") is None


def test_purge_removes_dead_credentials(conn, alice):
    with conn.cursor() as cur:
        cur.execute("UPDATE app.sessions SET created_at = now() - interval '40 days', "
                    "expires_at = now() - interval '1 day'")
        cur.execute("UPDATE app.login_tokens SET created_at = now() - interval '40 days', "
                    "expires_at = now() - interval '30 days'")
    sessions, tokens = purge_expired(conn)
    assert sessions == 1 and tokens == 1


# ---------------------------------------------------------------------------
# Isolation — the property that matters
# ---------------------------------------------------------------------------

def test_superusers_bypass_rls_which_is_why_these_tests_set_the_role(conn, alice):
    """Stated as a test because it nearly invalidated this whole section.

    PostgreSQL exempts superusers from row level security unconditionally, and
    the test harness connects as the database owner, which on a local install
    is a superuser. The isolation tests below originally ran without
    `app_role` and passed -- they would have passed with no policies at all.

    This asserts the bypass is real, so the reason the other tests set the role
    cannot be quietly removed as ceremony.
    """
    store.create_watchlist(conn, alice.user_id, "Alice's")
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_user_id', '', true)")
        cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        is_superuser = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM app.watchlists")
        visible = cur.fetchone()[0]
    if is_superuser:
        assert visible == 1, "expected the superuser bypass"
    with app_role(conn), conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_user_id', '', true)")
        cur.execute("SELECT count(*) FROM app.watchlists")
        assert cur.fetchone()[0] == 0, "ii_app must see nothing unscoped"


def test_a_user_sees_only_their_own_watchlists(conn, alice, bob):
    """Note what makes this meaningful: `list_watchlists` has NO user_id
    predicate. RLS is the filter. If the policy were missing, this returns
    both."""
    store.create_watchlist(conn, alice.user_id, "Alice's list")
    store.create_watchlist(conn, bob.user_id, "Bob's list")

    with app_role(conn):
        assert [w["name"] for w in store.list_watchlists(conn, alice.user_id)] \
            == ["Alice's list"]
        assert [w["name"] for w in store.list_watchlists(conn, bob.user_id)] \
            == ["Bob's list"]


def test_a_user_cannot_add_to_someone_elses_watchlist(conn, alice, bob, company):
    watchlist = store.create_watchlist(conn, alice.user_id, "Alice's")
    with app_role(conn), pytest.raises(store.NotFound):
        store.add_to_watchlist(conn, bob.user_id, watchlist, company)


def test_a_user_cannot_read_someone_elses_watchlist_items(conn, alice, bob, company):
    watchlist = store.create_watchlist(conn, alice.user_id, "Alice's")
    store.add_to_watchlist(conn, alice.user_id, watchlist, company)
    with app_role(conn):
        assert store.watchlist_items(conn, alice.user_id, watchlist) != []
        assert store.watchlist_items(conn, bob.user_id, watchlist) == []


def test_a_user_sees_only_their_own_portfolio(conn, alice, bob, company):
    store.add_holding(conn, alice.user_id, company, "100", "50000")
    with app_role(conn):
        assert len(store.portfolio(conn, alice.user_id)) == 1
        assert store.portfolio(conn, bob.user_id) == []


def test_a_user_sees_only_their_own_saved_screens(conn, alice, bob):
    store.save_screen(conn, alice.user_id, "Profitable",
                      [{"metric": "ROE", "op": "gte", "value": "0.15"}], FY, "INR")
    with app_role(conn):
        assert len(store.list_screens(conn, alice.user_id)) == 1
        assert store.list_screens(conn, bob.user_id) == []


def test_a_user_sees_only_their_own_alert_rules(conn, alice, bob, company):
    store.create_alert(conn, alice.user_id, "ROE", "lt", "0.10")
    with app_role(conn):
        assert len(store.list_alerts(conn, alice.user_id)) == 1
        assert store.list_alerts(conn, bob.user_id) == []


def test_an_unauthenticated_connection_sees_nothing(conn, alice, company):
    """The default must be no rows, not all rows. `current_setting(..., true)`
    returns NULL when unset and every policy evaluates false."""
    store.create_watchlist(conn, alice.user_id, "Alice's")
    with app_role(conn), conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_user_id', '', true)")
        cur.execute("SELECT count(*) FROM app.watchlists")
        assert cur.fetchone()[0] == 0


def test_the_user_scope_does_not_outlive_the_block(conn, alice):
    """On a pooled connection a leaked setting would serve the previous
    requester's data to the next one -- the worst available bug here."""
    with store.as_user(conn, alice.user_id):
        pass
    with conn.cursor() as cur:
        cur.execute("SELECT current_setting('app.current_user_id', true)")
        assert cur.fetchone()[0] in ("", None)


def test_referential_integrity_is_checked_in_the_application(conn, alice):
    """`watchlist_items.instrument_id` is deliberately not a foreign key, so
    the check has to be explicit rather than forgotten."""
    watchlist = store.create_watchlist(conn, alice.user_id, "Alice's")
    with pytest.raises(store.NotFound, match="no such instrument"):
        store.add_to_watchlist(conn, alice.user_id, watchlist, 999_999)


# ---------------------------------------------------------------------------
# Invariant 8: market data never depends on the user domain
# ---------------------------------------------------------------------------

def test_no_foreign_key_points_from_market_data_into_the_user_domain(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT tc.table_name, kcu.column_name
            FROM   information_schema.table_constraints tc
            JOIN   information_schema.key_column_usage kcu
                     ON kcu.constraint_name = tc.constraint_name
            JOIN   information_schema.constraint_column_usage ccu
                     ON ccu.constraint_name = tc.constraint_name
            WHERE  tc.constraint_type = 'FOREIGN KEY'
              AND  tc.table_schema = 'public'
              AND  ccu.table_schema = 'app'
            """)
        assert cur.fetchall() == []


def test_the_ingestion_role_cannot_read_the_user_domain(conn):
    """Invariant 8 as a privilege, not a promise. An ingestion job physically
    cannot join against a watchlist."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name FROM information_schema.role_table_grants
            WHERE  grantee = 'ii_ingest' AND table_schema = 'app'
            """)
        assert cur.fetchall() == []


# ---------------------------------------------------------------------------
# Alerts: edge-triggered, not level-triggered
# ---------------------------------------------------------------------------

def test_an_alert_fires_when_the_condition_becomes_true(conn, alice, company):
    store.create_alert(conn, alice.user_id, "ROE", "gte", "0.15",
                       instrument_id=company, fiscal_year=FY, currency="INR")
    report = alerts.evaluate(conn)
    assert report.rules_evaluated == 1
    assert report.delivered == 1
    assert len(store.deliveries(conn, alice.user_id)) == 1


def test_an_alert_does_not_fire_again_while_it_stays_true(conn, alice, company):
    """Edge-triggered, not level-triggered. Otherwise a user who sets one
    threshold gets the same message every day until they disable everything."""
    store.create_alert(conn, alice.user_id, "ROE", "gte", "0.15",
                       instrument_id=company, fiscal_year=FY, currency="INR")
    alerts.evaluate(conn)
    for _ in range(3):
        again = alerts.evaluate(conn)
        assert again.delivered == 0
        assert again.conditions_met == 1, "the condition should still hold"
    assert len(store.deliveries(conn, alice.user_id)) == 1


def test_an_alert_re_arms_and_fires_again_on_the_next_crossing(conn, alice, source, company):
    """A metric that genuinely crosses back and forth should alert each time.
    Suppressing that would be as wrong as repeating while true."""
    store.create_alert(conn, alice.user_id, "ROE", "gte", "0.15",
                       instrument_id=company, fiscal_year=FY, currency="INR")
    alerts.evaluate(conn)

    # Profit restated downward: ROE falls to 4%, condition goes false.
    worse = add_filing(conn, company, source, period_end=date(FY, 3, 31),
                       filed_at=utc(FY, 9, 1), ref="worse")
    add_fact(conn, company, source, worse, line_item="NET_PROFIT",
             value=Decimal(20), fiscal_year=FY, period_type="ANNUAL",
             known_from=utc(FY, 9, 1),
             period_start=date(FY - 1, 4, 1), period_end=date(FY, 3, 31))
    with conn.cursor() as cur:
        cur.execute("SELECT refresh_metric_values()")
    rearm = alerts.evaluate(conn)
    assert rearm.delivered == 0 and rearm.re_armed == 1

    # Restated back up: it crosses again and must deliver again.
    better = add_filing(conn, company, source, period_end=date(FY, 3, 31),
                        filed_at=utc(FY, 12, 1), ref="better")
    add_fact(conn, company, source, better, line_item="NET_PROFIT",
             value=Decimal(100), fiscal_year=FY, period_type="ANNUAL",
             known_from=utc(FY, 12, 1),
             period_start=date(FY - 1, 4, 1), period_end=date(FY, 3, 31))
    with conn.cursor() as cur:
        cur.execute("SELECT refresh_metric_values()")
    assert alerts.evaluate(conn).delivered == 1
    assert len(store.deliveries(conn, alice.user_id)) == 2


def test_an_alert_that_never_becomes_true_never_fires(conn, alice, company):
    store.create_alert(conn, alice.user_id, "ROE", "gte", "0.99",
                       instrument_id=company, fiscal_year=FY, currency="INR")
    report = alerts.evaluate(conn)
    assert report.conditions_met == 0 and report.delivered == 0


def test_a_disabled_rule_is_not_evaluated(conn, alice, company):
    rule = store.create_alert(conn, alice.user_id, "ROE", "gte", "0.15",
                              instrument_id=company, fiscal_year=FY, currency="INR")
    with store.as_user(conn, alice.user_id) as c, c.cursor() as cur:
        cur.execute("UPDATE app.alert_rules SET enabled = false WHERE rule_id = %s",
                    (rule,))
    assert alerts.evaluate(conn).rules_evaluated == 0


def test_a_universe_wide_rule_covers_every_company(conn, alice, company):
    """instrument_id null means "anything that starts meeting this", which is
    how a screen becomes an alert."""
    store.create_alert(conn, alice.user_id, "ROE", "gte", "0.15",
                       instrument_id=None, fiscal_year=FY, currency="INR")
    assert alerts.evaluate(conn).delivered == 1


def test_an_unknown_metric_cannot_become_a_rule(conn, alice):
    """Rather than creating a rule that can never fire and looks like it is
    simply not triggering."""
    with pytest.raises(store.NotFound, match="no such metric"):
        store.create_alert(conn, alice.user_id, "MADE_UP", "gte", "1")


def test_a_rule_with_a_corrupt_operator_is_skipped_loudly(conn, alice, company):
    """Cannot happen through the API. If it ever does, the rule is skipped and
    named rather than evaluated some other way."""
    store.create_alert(conn, alice.user_id, "ROE", "gte", "0.15",
                       instrument_id=company, fiscal_year=FY, currency="INR")
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE app.alert_rules DROP CONSTRAINT alert_rules_operator_check")
        cur.execute("UPDATE app.alert_rules SET operator = 'DROP TABLE'")
    report = alerts.evaluate(conn)
    assert report.skipped_rules and report.delivered == 0


def test_firing_and_sending_are_separate_steps(conn, alice, company):
    """A fired alert is durable before anyone tries to send it. If firing also
    sent the email, a failing provider would either lose the alert or stall
    the evaluation cycle."""
    store.create_alert(conn, alice.user_id, "ROE", "gte", "0.15",
                       instrument_id=company, fiscal_year=FY, currency="INR")
    alerts.evaluate(conn)

    pending = alerts.pending_deliveries(conn)
    assert len(pending) == 1
    assert pending[0]["email"] == "alice@example.com"

    alerts.mark_delivered(conn, pending[0]["delivery_id"])
    assert alerts.pending_deliveries(conn) == []
