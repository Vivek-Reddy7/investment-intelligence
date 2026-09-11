"""Fixed-window rate limiting.

Used for two things, and the second is the one worth reading about.

**Login links.** Unlimited `begin_login` is an email-bombing tool pointed at a
third party: an attacker floods someone else's inbox and burns our send quota
doing it. Limited per address AND per client, because limiting only by address
lets one client spray thousands of addresses, and limiting only by client lets
a botnet target one inbox.

**Historical screens.** A live screen reads a materialised table. A historical
screen recomputes the whole metric pivot for an arbitrary as-of date -- it is
unauthenticated, it is the headline feature, and the date is a free parameter.
So an attacker gets unlimited distinct, uncacheable, full-table computations
from a query string, which on a 100-compute-hour free tier is a cheap way to
take the site down. Nothing about the requests looks abnormal.

A deliberate trade in the login limiter: capping per address means an attacker
can exhaust a victim's quota and stop them requesting a NEW link. It does not
sign them out, and it does not invalidate a link they already have. Locking
someone briefly out of requesting an email is a smaller harm than letting
anyone flood their inbox, so the cap stays -- but the asymmetry is real and
should be a conscious choice rather than a default.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg


@dataclass(frozen=True)
class Limit:
    action: str
    attempts: int
    window: timedelta

    @property
    def description(self) -> str:
        return f"{self.attempts} per {int(self.window.total_seconds() // 60)} minutes"


# Per email address: generous enough for a genuine "didn't arrive, resend"
# without being a useful flooding tool.
LOGIN_PER_EMAIL = Limit("login:email", attempts=5, window=timedelta(hours=1))

# Per client: much tighter, because a single client requesting links for many
# different addresses is spraying, and there is no legitimate version of that.
LOGIN_PER_CLIENT = Limit("login:client", attempts=10, window=timedelta(hours=1))

# Historical screens. Enough for a person exploring dates, far short of enough
# to exhaust a monthly compute allowance.
HISTORICAL_SCREEN = Limit("screen:historical", attempts=60, window=timedelta(minutes=10))


class RateLimited(Exception):
    """Too many attempts. Carries how long to wait, and nothing else."""

    def __init__(self, limit: Limit, retry_after: timedelta) -> None:
        super().__init__(f"rate limited: {limit.description}")
        self.limit = limit
        self.retry_after = retry_after


def hash_subject(value: str) -> str:
    """Hash a subject before storing it.

    An email address or IP in this table would make it a visitor log — a
    record of who tried to sign in and from where, kept for no product
    reason. The limiter only needs to recognise the same subject again, which
    a digest gives.
    """
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def _window_start(limit: Limit, now: datetime) -> datetime:
    """Floor `now` to the current window.

    Flooring rather than sliding means the counter key is derivable from the
    clock alone, so there is no read-then-write race to lose.
    """
    seconds = int(limit.window.total_seconds())
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % seconds), tz=timezone.utc)


def check_and_count(
    conn: psycopg.Connection,
    limit: Limit,
    subject: str,
    *,
    now: datetime | None = None,
    hash_it: bool = True,
) -> int:
    """Record an attempt, or raise RateLimited. Returns attempts so far.

    The insert and the check are one statement, so two concurrent requests
    cannot both read "4 attempts" and both proceed. `ON CONFLICT DO UPDATE`
    returning the new count is what makes that atomic.
    """
    moment = now or datetime.now(timezone.utc)
    start = _window_start(limit, moment)
    key = hash_subject(subject) if hash_it else subject

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app.rate_limits (action, subject, window_start, attempts)
            VALUES (%s, %s, %s, 1)
            ON CONFLICT (action, subject, window_start) DO UPDATE
               SET attempts = app.rate_limits.attempts + 1
            RETURNING attempts
            """,
            (limit.action, key, start),
        )
        attempts = cur.fetchone()[0]

    if attempts > limit.attempts:
        raise RateLimited(limit, retry_after=(start + limit.window) - moment)
    return attempts


def peek(
    conn: psycopg.Connection, limit: Limit, subject: str,
    *, now: datetime | None = None, hash_it: bool = True,
) -> int:
    """Attempts so far in the current window, without recording one."""
    moment = now or datetime.now(timezone.utc)
    key = hash_subject(subject) if hash_it else subject
    with conn.cursor() as cur:
        cur.execute(
            "SELECT attempts FROM app.rate_limits "
            "WHERE action = %s AND subject = %s AND window_start = %s",
            (limit.action, key, _window_start(limit, moment)),
        )
        row = cur.fetchone()
    return row[0] if row else 0


def sweep(conn: psycopg.Connection) -> int:
    """Drop counters from closed windows. Run on the daily schedule."""
    with conn.cursor() as cur:
        cur.execute("SELECT app.sweep_rate_limits()")
        return cur.fetchone()[0]
