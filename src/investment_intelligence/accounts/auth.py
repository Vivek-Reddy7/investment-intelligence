"""Sign-in by emailed magic link. No passwords, anywhere.

There is no password column in the schema, no hashing, no comparison, no
transport. That removes the whole category: no reuse across sites, no weak
choices, no leaked hash to crack, no reset flow to abuse. The cost is a
dependency on email delivery, which is a smaller problem than the one it
replaces.

Three rules this module exists to keep:

1. **Only hashes are stored.** A leaked database must not yield usable
   sessions or usable login links. We have no legitimate reason to be able to
   reconstruct either.

2. **Tokens are compared in constant time**, and looked up BY their hash so
   the comparison happens inside the index rather than in Python. A
   byte-by-byte comparison on a secret leaks its prefix through timing.

3. **Every failure returns the same thing.** An unknown email, an expired
   token and an already-used token are indistinguishable to the caller.
   Distinguishing them turns the sign-in form into an account-enumeration
   oracle.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg

# A magic link is short-lived because it sits in an inbox, which is a less
# trustworthy place than a cookie jar.
LOGIN_TOKEN_TTL = timedelta(minutes=15)
SESSION_TTL = timedelta(days=30)
TOKEN_BYTES = 32   # 256 bits from secrets.token_urlsafe


class AuthError(Exception):
    """Sign-in failed. Deliberately says no more than that."""


def _hash(token: str) -> bytes:
    """SHA-256 of the token. Not a password hash, and it should not be one.

    A password needs a slow KDF because it has low entropy and is guessable.
    These tokens are 256 bits of `secrets` output: brute force is not the
    threat, and a slow hash on every request would be cost for no benefit.
    What is needed is that the stored value cannot be reversed, which a plain
    digest gives.
    """
    return hashlib.sha256(token.encode()).digest()


def normalise_email(email: str) -> str:
    """Lowercase and trim. The schema CHECK enforces the same thing.

    Without this, `A@b.com` and `a@b.com` become two accounts for one person,
    and the second one silently has none of their data.
    """
    cleaned = email.strip().lower()
    if len(cleaned) < 3 or cleaned.count("@") != 1 or cleaned.startswith("@") \
            or cleaned.endswith("@"):
        raise AuthError("not a usable email address")
    return cleaned


@dataclass(frozen=True)
class LoginLink:
    """The token to email, and when it dies.

    Returned once and never retrievable again -- only its hash is stored.
    """
    token: str
    email: str
    expires_at: datetime


def begin_login(conn: psycopg.Connection, email: str) -> LoginLink:
    """Issue a single-use login token for an email address.

    Deliberately does NOT check whether the address has an account, and does
    not create one. Both would make this an enumeration oracle: the caller
    learns nothing from the response either way, and the account is created on
    successful consumption instead.
    """
    address = normalise_email(email)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    expires = datetime.now(timezone.utc) + LOGIN_TOKEN_TTL

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO app.login_tokens (token_hash, email, expires_at) "
            "VALUES (%s, %s, %s)",
            (_hash(token), address, expires),
        )
    return LoginLink(token=token, email=address, expires_at=expires)


@dataclass(frozen=True)
class Session:
    token: str
    user_id: str
    expires_at: datetime


def complete_login(
    conn: psycopg.Connection, token: str, *, user_agent: str | None = None
) -> Session:
    """Consume a login token and return a session. Creates the user if new.

    The token is looked up by hash, so an attacker with a guess gets one index
    probe and no timing signal from a comparison loop. `consumed_at` is set in
    the same statement that selects it, so two simultaneous presentations
    cannot both succeed.
    """
    digest = _hash(token)
    now = datetime.now(timezone.utc)

    with conn.cursor() as cur:
        # Claim the token atomically. The WHERE clause is the authorisation
        # check: unknown, expired and already-consumed all match zero rows and
        # are therefore indistinguishable from here on.
        cur.execute(
            """
            UPDATE app.login_tokens
            SET    consumed_at = %s
            WHERE  token_hash = %s
              AND  consumed_at IS NULL
              AND  expires_at > %s
            RETURNING email
            """,
            (now, digest, now),
        )
        row = cur.fetchone()
        if row is None:
            raise AuthError("that sign-in link is not valid")
        email = row[0]

        # RLS is FORCEd on app.users, so an insert must run with the session
        # variable set -- and for a brand-new user we do not yet have an id.
        # Resolved by generating it here rather than letting the default fire,
        # so the policy can be satisfied before the row exists.
        cur.execute("SELECT set_config('app.current_user_id', '', true)")
        cur.execute("SELECT user_id FROM app.users WHERE email = %s", (email,))
        found = cur.fetchone()
        if found is None:
            cur.execute("SELECT gen_random_uuid()")
            user_id = cur.fetchone()[0]
            cur.execute(
                "SELECT set_config('app.current_user_id', %s, true)", (str(user_id),))
            cur.execute(
                "INSERT INTO app.users (user_id, email, last_seen_at) "
                "VALUES (%s, %s, %s)", (user_id, email, now))
        else:
            user_id = found[0]
            cur.execute(
                "SELECT set_config('app.current_user_id', %s, true)", (str(user_id),))
            cur.execute(
                "UPDATE app.users SET last_seen_at = %s WHERE user_id = %s",
                (now, user_id))

        session_token = secrets.token_urlsafe(TOKEN_BYTES)
        expires = now + SESSION_TTL
        cur.execute(
            "INSERT INTO app.sessions (token_hash, user_id, expires_at, user_agent) "
            "VALUES (%s, %s, %s, %s)",
            (_hash(session_token), user_id, expires, user_agent),
        )

    return Session(token=session_token, user_id=str(user_id), expires_at=expires)


def authenticate(conn: psycopg.Connection, token: str | None) -> str | None:
    """Resolve a session token to a user id, or None.

    Returns None for absent, unknown and expired tokens alike. The caller gets
    "not signed in" and nothing more.
    """
    if not token:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT user_id FROM app.sessions "
            "WHERE token_hash = %s AND expires_at > now()",
            (_hash(token),),
        )
        row = cur.fetchone()
    return str(row[0]) if row else None


def sign_out(conn: psycopg.Connection, token: str) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM app.sessions WHERE token_hash = %s", (_hash(token),))


def purge_expired(conn: psycopg.Connection) -> tuple[int, int]:
    """Delete expired sessions and login tokens. Returns (sessions, tokens).

    Run on the daily schedule. Not a security control -- an expired token is
    already refused by `authenticate` -- but an unbounded table of dead
    credentials is an unnecessary thing to be holding.
    """
    with conn.cursor() as cur:
        cur.execute("DELETE FROM app.sessions WHERE expires_at < now()")
        sessions = cur.rowcount
        cur.execute(
            "DELETE FROM app.login_tokens "
            "WHERE expires_at < now() - interval '7 days'")
        tokens = cur.rowcount
    return sessions, tokens


def constant_time_equals(a: str, b: str) -> bool:
    """Exposed for callers comparing any other secret.

    Not used in the lookup paths above, which compare inside a hashed index
    instead -- that is strictly better, because no candidate value ever reaches
    application code.
    """
    return hmac.compare_digest(a.encode(), b.encode())
