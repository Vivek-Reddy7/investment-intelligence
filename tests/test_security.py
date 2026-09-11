"""Phase 15: security hardening, asserted rather than asserted-to.

    No secrets in the repository.
    The application role cannot mutate history.
    Dependency scanning in CI.

The privilege audit in §1 is the centrepiece. Earlier phases tested individual
claims — `ii_app` cannot insert facts, no role can delete one. Those are
necessary and they are not sufficient: they check the grants we thought to
check. This enumerates **every** grant in the database and compares it to an
expected set, so a migration that over-grants fails even if nobody thought to
write a test for that table.
"""

from __future__ import annotations

import re
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from conftest import app_role
from investment_intelligence.accounts import ratelimit
from investment_intelligence.accounts.auth import AuthError, begin_login
from investment_intelligence.accounts.ratelimit import (
    HISTORICAL_SCREEN,
    LOGIN_PER_CLIENT,
    LOGIN_PER_EMAIL,
    RateLimited,
    check_and_count,
    hash_subject,
)

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1. The privilege audit
# ---------------------------------------------------------------------------

# Every privilege any application role is expected to hold, as
# {role: {schema: {privilege: {tables...}}}}. Written out rather than
# computed, because the point is that a human decided each line.
#
# `public` is market data: append-only history. `app` is user state: mutable,
# and kept apart by row level security rather than by privilege.
EXPECTED_GRANTS = {
    "ii_app": {
        # Serving reads market data and writes none of it, so a bug in the
        # read API cannot corrupt the store (invariant 9, taken further).
        "public": {"SELECT": "ALL"},
        "app": {"SELECT": "ALL", "INSERT": "ALL", "UPDATE": "ALL", "DELETE": "ALL"},
    },
    "ii_ingest": {
        "public": {
            "SELECT": "ALL",
            # Facts are insert-only; operational and reference tables are
            # mutable. The asymmetry is the whole design.
            "INSERT": {"instrument_identifiers", "instrument_lifecycle",
                       "financial_facts", "corporate_actions", "filings",
                       "ingestion_runs", "tracked_instruments", "sources",
                       "line_items", "metric_values", "quality_findings",
                       "instrument_external_ids", "ingestion_rejections",
                       "ingestion_schedule"},
            "UPDATE": {"ingestion_runs", "tracked_instruments", "sources",
                       "line_items", "instrument_external_ids",
                       "ingestion_schedule"},
            "DELETE": {"metric_values", "quality_findings"},
        },
        # Invariant 8: nothing at all on the user domain.
        "app": {},
    },
    "ii_alerts": {
        "public": {"SELECT": "ALL"},
        "app": {
            "SELECT": {"alert_rules", "alert_state", "alert_deliveries"},
            "INSERT": {"alert_rules", "alert_state", "alert_deliveries"},
            "UPDATE": {"alert_rules", "alert_state", "alert_deliveries"},
        },
    },
}


def _actual_grants(conn, role: str) -> dict[str, dict[str, set[str]]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_schema, privilege_type, table_name
            FROM   information_schema.role_table_grants
            WHERE  grantee = %s AND table_schema IN ('public', 'app')
            """,
            (role,),
        )
        out: dict[str, dict[str, set[str]]] = {}
        for schema, privilege, table in cur.fetchall():
            out.setdefault(schema, {}).setdefault(privilege, set()).add(table)
        return out


@pytest.mark.parametrize("role", sorted(EXPECTED_GRANTS))
def test_no_role_holds_a_privilege_nobody_decided_on(conn, role):
    """The audit. Enumerates everything, not just what we remembered to check."""
    actual = _actual_grants(conn, role)
    expected = EXPECTED_GRANTS[role]

    for schema, privileges in actual.items():
        allowed = expected.get(schema, {})
        unexpected_privs = set(privileges) - set(allowed)
        assert not unexpected_privs, (
            f"{role} holds {sorted(unexpected_privs)} on schema {schema}, "
            "which nothing in EXPECTED_GRANTS allows")

        for privilege, tables in privileges.items():
            permitted = allowed[privilege]
            if permitted == "ALL":
                continue
            over = tables - permitted
            assert not over, (
                f"{role} holds {privilege} on {sorted(over)} in {schema}; "
                "add it to EXPECTED_GRANTS deliberately or revoke it")


def test_the_ingestion_role_holds_nothing_on_the_user_domain(conn):
    """Invariant 8, stated on its own because it is the one that would be
    easiest to break by a convenient GRANT ... ON ALL TABLES."""
    assert _actual_grants(conn, "ii_ingest").get("app", {}) == {}


def test_no_application_role_can_alter_the_schema(conn):
    """DDL belongs to the migration role. An application role that can DROP a
    trigger can defeat the append-only guarantee."""
    with conn.cursor() as cur:
        for role in ("ii_app", "ii_ingest", "ii_alerts"):
            cur.execute("SELECT has_schema_privilege(%s, 'public', 'CREATE')", (role,))
            assert cur.fetchone()[0] is False, f"{role} can create objects in public"


def test_no_application_role_is_a_superuser_or_bypasses_rls(conn):
    """A superuser ignores row level security entirely, which would silently
    undo every isolation policy. The Phase 13 tests learned this the hard
    way."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname IN ('ii_app', 'ii_ingest', 'ii_alerts', 'ii_web')")
        for name, superuser, bypass in cur.fetchall():
            assert not superuser, f"{name} is a superuser"
            assert not bypass, f"{name} can bypass row level security"


# ---------------------------------------------------------------------------
# 2. Secrets
# ---------------------------------------------------------------------------

# Patterns for credentials that must never be committed. Deliberately narrow:
# a scanner that fires on the word "password" in a comment gets disabled.
SECRET_PATTERNS = [
    (re.compile(r"postgres(?:ql)?://[^\s:@/]+:(?P<secret>[^\s:@/]+)@", re.I),
     "database URL with a password"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "API key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
]

SCANNED_SUFFIXES = {".py", ".ts", ".tsx", ".sql", ".mjs", ".js", ".json",
                    ".yml", ".yaml", ".md", ".example", ".sh"}

# A placeholder in the password position is not a credential. All-caps with
# underscores is the universal shape for one (REPLACE_ME, CHANGE_ME,
# YOUR_PASSWORD) and is a shape a real password essentially never takes, so
# recognising it keeps the pattern strict everywhere else. Preferable to
# allow-listing each example string, which would go stale.
PLACEHOLDER = re.compile(r"^[A-Z][A-Z0-9_]{3,}$")


# Exact strings that match a pattern above and have been reviewed as not
# secret. An allow-list of reviewed exceptions rather than a looser pattern:
# weakening the regex to accommodate these would stop it catching a real one,
# and every entry here was a decision somebody made.
REVIEWED_EXCEPTIONS = {
    # CI service container. Ephemeral, reachable only from inside the job, and
    # the password is set by the same file that uses it — there is nothing to
    # leak.
    "postgresql://postgres:postgres@",
}


def _tracked_files() -> list[Path]:
    listing = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.split()
    return [ROOT / name for name in listing]


def test_no_committed_file_contains_a_credential():
    """Scans what git actually tracks, not the working directory.

    An untracked `.env.local` is fine and expected; the risk is a credential
    that has been committed, because it stays in history after being deleted.
    """
    offenders = []
    for path in _tracked_files():
        if path.suffix not in SCANNED_SUFFIXES or not path.exists():
            continue
        text = path.read_text(errors="ignore")
        for pattern, label in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                if match.group() in REVIEWED_EXCEPTIONS:
                    continue
                captured = match.groupdict().get("secret")
                if captured and PLACEHOLDER.fullmatch(captured):
                    continue
                offenders.append(f"{path.relative_to(ROOT)}: {label} — {match.group()[:40]}")
    assert offenders == [], "credentials in tracked files:\n" + "\n".join(offenders)


def test_every_reviewed_exception_is_still_used():
    """A stale exception is a hole waiting for a collision.

    If a reviewed string no longer appears anywhere, the entry should go —
    otherwise the allow-list quietly grows into a list of things the scanner
    ignores for reasons nobody remembers.
    """
    corpus = "\n".join(
        p.read_text(errors="ignore") for p in _tracked_files()
        if p.suffix in SCANNED_SUFFIXES and p.exists())
    unused = [e for e in REVIEWED_EXCEPTIONS if e not in corpus]
    assert unused == [], f"remove these stale exceptions: {unused}"


def test_env_files_are_ignored():
    ignored = (ROOT / ".gitignore").read_text()
    assert ".env" in ignored
    assert "web/.env.local" in ignored


def test_the_example_environment_file_is_tracked_and_holds_no_credential():
    """Two failures at once, found by the stale-exception test.

    The example was named `.env.local.example`, which `.gitignore`'s `.env.*`
    glob swallowed — so it was never committed and nobody could see which
    variables the app needs. And it carried a real (if local) password, which
    an example file should never do regardless of who can reach it.

    Now tracked under a name the glob does not match, with a placeholder.
    """
    example = ROOT / "web" / "env.example"
    assert example in _tracked_files(), "the example env file is not tracked"
    text = example.read_text()
    assert "DATABASE_URL" in text
    assert "REPLACE_ME" in text
    for pattern, _ in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            captured = match.groupdict().get("secret")
            assert captured and PLACEHOLDER.fullmatch(captured), \
                f"the example file contains a credential: {match.group()}"


@pytest.mark.parametrize("secret,should_flag", [
    ("REPLACE_ME", False),
    ("CHANGE_ME", False),
    ("YOUR_PASSWORD", False),
    # Real-looking secrets must still be caught, including in an example file.
    ("devonly", True),
    ("hunter2", True),
    ("Tr0ub4dor3", True),
    ("ABCdef123", True),
])
def test_the_placeholder_rule_does_not_blunt_the_scanner(secret, should_flag):
    """The exception has to be narrow enough that it cannot be used to smuggle
    a real credential past the scanner.

    The URLs are assembled from parts rather than written out, because the
    scanner scans this file too and complete literals here would trip it --
    correctly. Excluding this file from the scan would have been the easier
    fix and would have left a hole exactly where someone might hide something.
    """
    url = "postgres" + "ql://ii_web:" + secret + "@localhost/db"
    pattern = SECRET_PATTERNS[0][0]
    match = pattern.search(url)
    assert match, url
    captured = match.group("secret")
    flagged = not PLACEHOLDER.fullmatch(captured)
    assert flagged is should_flag, f"{captured}: flagged={flagged}"


def test_no_env_file_is_tracked():
    tracked = {p.name for p in _tracked_files()}
    for name in tracked:
        assert not name.startswith(".env") or name.endswith(".example"), \
            f"{name} is tracked and may hold real values"


# ---------------------------------------------------------------------------
# 3. Rate limiting
# ---------------------------------------------------------------------------

def test_a_login_flood_against_one_address_is_stopped(conn):
    """Phase 13 left this open: unlimited `begin_login` is an email-bombing
    tool aimed at a third party, and it burns our send quota."""
    for _ in range(LOGIN_PER_EMAIL.attempts):
        begin_login(conn, "victim@example.com")
    with pytest.raises(RateLimited):
        begin_login(conn, "victim@example.com")


def test_case_variation_does_not_multiply_the_quota(conn):
    """`Victim@x.com` and `victim@x.com` must share a counter, or the limit is
    trivially bypassed by changing capitalisation."""
    for i in range(LOGIN_PER_EMAIL.attempts):
        variant = "Victim@Example.com" if i % 2 else "victim@example.com"
        begin_login(conn, variant)
    with pytest.raises(RateLimited):
        begin_login(conn, "VICTIM@EXAMPLE.COM")


def test_one_client_cannot_spray_many_addresses(conn):
    """Limiting only per address lets a single client target thousands of
    different inboxes, each within its own quota."""
    with pytest.raises(RateLimited) as exc:
        for i in range(LOGIN_PER_CLIENT.attempts + 1):
            begin_login(conn, f"target{i}@example.com", client="198.51.100.7")
    assert exc.value.limit is LOGIN_PER_CLIENT


def test_different_clients_do_not_share_a_quota(conn):
    for i in range(LOGIN_PER_CLIENT.attempts):
        begin_login(conn, f"a{i}@example.com", client="198.51.100.7")
    # A different client is unaffected.
    begin_login(conn, "elsewhere@example.com", client="203.0.113.9")


def test_the_expensive_historical_screen_path_is_limited(conn):
    """The vector specific to this product.

    A live screen reads a materialised table; a historical screen recomputes
    the whole metric pivot for an arbitrary as-of date. It is
    unauthenticated, it is the headline feature, and the date is a free
    parameter — so an attacker gets unlimited distinct, uncacheable,
    full-table computations from a query string. On a 100-compute-hour free
    tier that is a cheap outage, and the requests look entirely normal.
    """
    for _ in range(HISTORICAL_SCREEN.attempts):
        check_and_count(conn, HISTORICAL_SCREEN, "198.51.100.7")
    with pytest.raises(RateLimited):
        check_and_count(conn, HISTORICAL_SCREEN, "198.51.100.7")


def test_a_rate_limited_error_says_how_long_to_wait(conn):
    with pytest.raises(RateLimited) as exc:
        for _ in range(LOGIN_PER_EMAIL.attempts + 1):
            begin_login(conn, "wait@example.com")
    assert timedelta(0) < exc.value.retry_after <= LOGIN_PER_EMAIL.window


def test_subjects_are_stored_hashed_so_the_table_is_not_a_visitor_log(conn):
    """An email address or IP in this table would be a record of who tried to
    sign in and from where, kept for no product reason."""
    begin_login(conn, "private@example.com", client="198.51.100.7")
    with conn.cursor() as cur:
        cur.execute("SELECT subject FROM app.rate_limits")
        subjects = [r[0] for r in cur.fetchall()]
    assert subjects
    for subject in subjects:
        assert "private@example.com" not in subject
        assert "198.51.100.7" not in subject
        assert re.fullmatch(r"[0-9a-f]{64}", subject), "expected a sha-256 hex digest"


def test_the_counter_is_atomic(conn):
    """Insert and increment are one statement, so two concurrent requests
    cannot both read 'four attempts' and both proceed."""
    counts = [check_and_count(conn, HISTORICAL_SCREEN, "atomic") for _ in range(5)]
    assert counts == [1, 2, 3, 4, 5]


def test_the_window_rolls_over(conn):
    from datetime import datetime, timezone
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    for _ in range(LOGIN_PER_EMAIL.attempts):
        check_and_count(conn, LOGIN_PER_EMAIL, "roll", now=base)
    with pytest.raises(RateLimited):
        check_and_count(conn, LOGIN_PER_EMAIL, "roll", now=base)
    # A later window starts fresh.
    later = base + LOGIN_PER_EMAIL.window * 2
    assert check_and_count(conn, LOGIN_PER_EMAIL, "roll", now=later) == 1


def test_sweeping_removes_closed_windows(conn):
    from datetime import datetime, timezone
    check_and_count(conn, LOGIN_PER_EMAIL, "old",
                    now=datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert ratelimit.sweep(conn) >= 1


def test_the_rate_limiter_is_not_row_level_secured(conn, app_user_id=None):
    """Deliberate: a limiter that could only see its own user's attempts would
    not be a limiter. Asserted so the exception is visible rather than looking
    like an oversight next to the RLS-protected tables around it."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT relrowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'app' AND c.relname = 'rate_limits'")
        assert cur.fetchone()[0] is False


# ---------------------------------------------------------------------------
# 4. Input validation, from the outside
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    "'; DROP TABLE app.users; --",
    "alice@example.com'; DELETE FROM app.sessions; --",
    "\x00nul@example.com",
    "a" * 500 + "@example.com",
])
def test_hostile_email_input_is_refused_or_neutralised(conn, payload):
    """Either rejected as unusable or stored as an inert value. What must not
    happen is execution."""
    try:
        begin_login(conn, payload)
    except (AuthError, RateLimited):
        pass
    except Exception as exc:  # noqa: BLE001 - a DB error is acceptable, a breach is not
        assert "syntax" not in str(exc).lower()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM app.sessions")
        cur.fetchone()
        cur.execute("SELECT to_regclass('app.users') IS NOT NULL")
        assert cur.fetchone()[0], "app.users no longer exists"


def test_a_subject_is_never_interpolated_into_sql(conn):
    """The rate limiter keys on caller-supplied strings, so this is the other
    place hostile input reaches a query."""
    check_and_count(conn, HISTORICAL_SCREEN, "'; DROP TABLE app.rate_limits; --")
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('app.rate_limits') IS NOT NULL")
        assert cur.fetchone()[0]
