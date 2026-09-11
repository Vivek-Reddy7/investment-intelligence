"""User-owned data: watchlists, saved screens, portfolio entries, alert rules.

Every function takes a `user_id` and sets `app.current_user_id` before
touching anything. That is not belt-and-braces over a WHERE clause -- there
are deliberately NO user_id filters in the queries below. The row level
security policies in migration 020 are the filter.

The reason is that a forgotten WHERE clause leaks silently: the page renders,
with someone else's holdings on it. Under RLS the same mistake returns nothing,
which is visible immediately. So the filtering lives where forgetting it fails
closed.

`as_user` is the only way to reach this data, and it is a context manager so
the setting cannot outlive the operation on a pooled connection.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Iterator

import psycopg


class NotFound(Exception):
    """Either it does not exist or it is not yours. Deliberately the same.

    Distinguishing them tells an attacker which ids are real.
    """


@contextmanager
def as_user(conn: psycopg.Connection, user_id: str) -> Iterator[psycopg.Connection]:
    """Scope a connection to one user for the duration of the block.

    `set_config(..., true)` makes the setting local to the transaction, so it
    cannot leak to the next request that borrows this pooled connection --
    which would be the worst possible bug in this file.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_user_id', %s, true)", (user_id,))
    try:
        yield conn
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_user_id', '', true)")


# ---------------------------------------------------------------------------
# Watchlists
# ---------------------------------------------------------------------------

def create_watchlist(conn, user_id: str, name: str) -> str:
    with as_user(conn, user_id) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO app.watchlists (user_id, name) VALUES (%s, %s) "
            "RETURNING watchlist_id", (user_id, name))
        return str(cur.fetchone()[0])


def list_watchlists(conn, user_id: str) -> list[dict]:
    with as_user(conn, user_id) as c, c.cursor() as cur:
        # No user_id predicate. RLS supplies it.
        cur.execute(
            """
            SELECT w.watchlist_id, w.name, w.created_at,
                   count(i.instrument_id) AS items
            FROM   app.watchlists w
            LEFT   JOIN app.watchlist_items i USING (watchlist_id)
            GROUP  BY w.watchlist_id, w.name, w.created_at
            ORDER  BY w.created_at
            """)
        return [dict(zip([d.name for d in cur.description], r)) for r in cur.fetchall()]


def add_to_watchlist(conn, user_id: str, watchlist_id: str,
                     instrument_id: int, note: str | None = None) -> None:
    """Add an instrument, checking it exists.

    `watchlist_items.instrument_id` is deliberately not a foreign key into
    `public.instruments` -- see migration 020 -- so referential integrity is
    checked here. That is the price of keeping the market and user domains
    separable, and it has to be paid explicitly rather than forgotten.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM instruments WHERE instrument_id = %s",
                    (instrument_id,))
        if cur.fetchone() is None:
            raise NotFound(f"no such instrument: {instrument_id}")

    with as_user(conn, user_id) as c:
        # A savepoint, because an RLS WITH CHECK violation raises rather than
        # returning zero rows -- which is the better behaviour, and it aborts
        # the transaction. Without the savepoint one refused insert would
        # poison every later statement in the request.
        try:
            with c.transaction(), c.cursor() as cur:
                cur.execute(
                    "INSERT INTO app.watchlist_items "
                    "(watchlist_id, instrument_id, note) VALUES (%s, %s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (watchlist_id, instrument_id, note))
                inserted = cur.rowcount
        except psycopg.errors.InsufficientPrivilege as exc:
            # The policy refused it: this watchlist is not the caller's.
            # Reported as NotFound so it is indistinguishable from a
            # nonexistent id, which is what stops id enumeration.
            raise NotFound("no such watchlist") from exc

        if inserted == 0:
            # ON CONFLICT DO NOTHING also reports zero for a row that is
            # already there, which is success. Only an unreadable watchlist is
            # an error, and RLS makes those two distinguishable by a SELECT.
            with c.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM app.watchlist_items "
                    "WHERE watchlist_id = %s AND instrument_id = %s",
                    (watchlist_id, instrument_id))
                if cur.fetchone() is None:
                    raise NotFound("no such watchlist")


def watchlist_items(conn, user_id: str, watchlist_id: str) -> list[dict]:
    with as_user(conn, user_id) as c, c.cursor() as cur:
        cur.execute(
            """
            SELECT i.instrument_id, i.added_at, i.note,
                   (SELECT value FROM instrument_external_ids
                     WHERE instrument_id = i.instrument_id
                       AND scheme = 'US_TICKER') AS ticker
            FROM   app.watchlist_items i
            WHERE  i.watchlist_id = %s
            ORDER  BY i.added_at
            """, (watchlist_id,))
        return [dict(zip([d.name for d in cur.description], r)) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Saved screens
# ---------------------------------------------------------------------------

def save_screen(conn, user_id: str, name: str, criteria: list[dict],
                fiscal_year: int, currency: str, as_of: str | None = None) -> str:
    """Save a screen definition.

    `as_of` null means "always current"; a value pins it. Those are genuinely
    different saved objects -- "profitable companies" versus "what looked
    profitable in June 2020" -- so the distinction is stored rather than
    inferred.
    """
    with as_user(conn, user_id) as c, c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app.saved_screens
                (user_id, name, criteria, fiscal_year, currency, as_of)
            VALUES (%s, %s, %s::jsonb, %s, %s, %s)
            RETURNING screen_id
            """,
            (user_id, name, json.dumps(criteria), fiscal_year, currency, as_of))
        return str(cur.fetchone()[0])


def list_screens(conn, user_id: str) -> list[dict]:
    with as_user(conn, user_id) as c, c.cursor() as cur:
        cur.execute(
            "SELECT screen_id, name, criteria, fiscal_year, currency, as_of, "
            "created_at FROM app.saved_screens ORDER BY created_at")
        return [dict(zip([d.name for d in cur.description], r)) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------

def add_holding(conn, user_id: str, instrument_id: int, quantity: str,
                cost_basis: str | None = None, currency: str = "INR",
                acquired_on=None, note: str | None = None) -> str:
    """Record a holding. Bookkeeping only: no valuation, no P&L.

    Prices are behind the private path (Phase 3), so there is nothing to mark
    this to. Storing a cost basis and quantity without pretending to value
    them is the honest version.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM instruments WHERE instrument_id = %s",
                    (instrument_id,))
        if cur.fetchone() is None:
            raise NotFound(f"no such instrument: {instrument_id}")

    with as_user(conn, user_id) as c, c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app.portfolio_entries
                (user_id, instrument_id, quantity, cost_basis, currency,
                 acquired_on, note)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING entry_id
            """,
            (user_id, instrument_id, quantity, cost_basis, currency,
             acquired_on, note))
        return str(cur.fetchone()[0])


def portfolio(conn, user_id: str) -> list[dict]:
    with as_user(conn, user_id) as c, c.cursor() as cur:
        cur.execute(
            """
            SELECT p.entry_id, p.instrument_id, p.quantity, p.cost_basis,
                   p.currency, p.acquired_on, p.note,
                   (SELECT value FROM instrument_external_ids
                     WHERE instrument_id = p.instrument_id
                       AND scheme = 'US_TICKER') AS ticker
            FROM   app.portfolio_entries p
            ORDER  BY p.created_at
            """)
        return [dict(zip([d.name for d in cur.description], r)) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Alert rules
# ---------------------------------------------------------------------------

def create_alert(conn, user_id: str, metric_code: str, operator: str,
                 threshold: str, instrument_id: int | None = None,
                 fiscal_year: int | None = None, currency: str = "INR") -> str:
    """Create an alert rule on a condition the user chooses.

    Validated against the `metrics` table rather than a Python list, so a
    metric added by a migration is alertable immediately and a typo fails here
    instead of producing a rule that can never fire.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM metrics WHERE metric_code = %s", (metric_code,))
        if cur.fetchone() is None:
            raise NotFound(f"no such metric: {metric_code}")

    with as_user(conn, user_id) as c, c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app.alert_rules
                (user_id, instrument_id, metric_code, operator, threshold,
                 fiscal_year, currency)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING rule_id
            """,
            (user_id, instrument_id, metric_code, operator, threshold,
             fiscal_year, currency))
        return str(cur.fetchone()[0])


def list_alerts(conn, user_id: str) -> list[dict]:
    with as_user(conn, user_id) as c, c.cursor() as cur:
        cur.execute(
            "SELECT rule_id, instrument_id, metric_code, operator, threshold, "
            "fiscal_year, currency, enabled, created_at "
            "FROM app.alert_rules ORDER BY created_at")
        return [dict(zip([d.name for d in cur.description], r)) for r in cur.fetchall()]


def deliveries(conn, user_id: str, limit: int = 50) -> list[dict]:
    with as_user(conn, user_id) as c, c.cursor() as cur:
        cur.execute(
            "SELECT delivery_id, rule_id, instrument_id, observed, threshold, "
            "fired_at, delivered_at FROM app.alert_deliveries "
            "ORDER BY fired_at DESC LIMIT %s", (limit,))
        return [dict(zip([d.name for d in cur.description], r)) for r in cur.fetchall()]
