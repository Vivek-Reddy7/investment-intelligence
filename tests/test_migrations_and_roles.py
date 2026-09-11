"""Migration behaviour and role privileges.

ADR 005 claims the application role cannot rewrite history. The triggers
already make that true for everyone, but the privilege grant is the layer that
survives someone dropping a trigger, so it deserves its own test.

The roles are NOLOGIN, so these tests use SET ROLE, which applies the same
privilege checks as connecting as that role would.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import psycopg
import pytest

from conftest import add_fact, add_filing, utc
from investment_intelligence.db import MigrationDrift, discover, migrate


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

def test_migrations_are_idempotent(conn):
    """Re-running applies nothing. Every migration must be safe to see twice,
    because a partially failed run gets retried."""
    assert migrate(conn) == []


def test_all_migrations_are_recorded(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM schema_migrations")
        assert cur.fetchone()[0] == len(discover())


def test_editing_an_applied_migration_is_detected(conn, tmp_path: Path):
    """Editing an applied migration means the schema in front of you is not
    the schema that ran, and the next environment gets something different.
    The runner refuses rather than silently diverging."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO schema_migrations (filename, sha256) "
            "VALUES ('900_fake.sql', 'a-hash-that-will-not-match')"
        )
    (tmp_path / "900_fake.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationDrift):
        migrate(conn, tmp_path)


def test_no_migration_contains_a_down_step(conn):
    """Forward only, deliberately. A rollback that drops a column drops facts,
    and the facts are the product."""
    for migration in discover():
        sql = migration.sql.lower()
        assert "drop table" not in sql, migration.filename
        assert "drop column" not in sql, migration.filename


# ---------------------------------------------------------------------------
# Privileges
# ---------------------------------------------------------------------------

@pytest.fixture
def seeded(conn, instrument, source):
    filing = add_filing(conn, instrument, source, period_end=date(2026, 9, 30),
                        filed_at=utc(2026, 10, 14), ref="privs")
    add_fact(conn, instrument, source, filing, line_item="REVENUE", value=1000,
             fiscal_year=2026, period_type="Q2", known_from=utc(2026, 10, 14))
    return instrument, source, filing


def test_app_role_can_read_facts(conn, seeded):
    with conn.cursor() as cur:
        cur.execute("SET ROLE ii_app")
        cur.execute("SELECT count(*) FROM facts_as_of(%s)", (utc(2027, 1, 1),))
        assert cur.fetchone()[0] >= 1


def test_default_as_of_excludes_facts_stamped_in_the_future(conn, seeded):
    """Found by a failing test that was wrong for the right reason.

    The fixture stamps known_from in late 2026, which is ahead of the current
    clock, and facts_as_of() defaulting to now() correctly returned nothing.
    That is the invariant working: a fact we have not learned yet cannot
    appear, and "not learned yet" includes a bad clock or a mis-stamped
    ingest. Worth an explicit test so the behaviour is intended rather than
    incidental.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM facts_as_of()")
        assert cur.fetchone()[0] == 0


def test_app_role_cannot_insert_facts(conn, seeded):
    """Invariant 9 taken further: the serving path writes nothing at all, so a
    bug in the read API cannot corrupt the store."""
    instrument, source, filing = seeded
    with conn.cursor() as cur:
        cur.execute("SET ROLE ii_app")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                "INSERT INTO financial_facts (instrument_id, basis, fiscal_year, "
                "period_type, line_item, period_start, period_end, value, "
                "known_from, filing_id, source_id) VALUES "
                "(%s, 'CONSOLIDATED', 2026, 'Q3', 'REVENUE', '2026-10-01', "
                "'2026-12-31', 1, now(), %s, %s)",
                (instrument, filing, source),
            )


@pytest.mark.parametrize("role", ["ii_app", "ii_ingest"])
def test_no_role_is_granted_delete_anywhere(conn, role):
    """Nothing in this system has a legitimate reason to delete a row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name FROM information_schema.role_table_grants
            WHERE  grantee = %s AND privilege_type = 'DELETE'
            """,
            (role,),
        )
        assert cur.fetchall() == []


def test_app_role_is_granted_no_update_on_fact_tables(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name FROM information_schema.role_table_grants
            WHERE  grantee = 'ii_app' AND privilege_type IN ('UPDATE', 'INSERT')
            """
        )
        assert cur.fetchall() == []


def test_ingest_role_can_insert_but_not_update_facts(conn, seeded):
    instrument, source, filing = seeded
    with conn.cursor() as cur:
        cur.execute("SET ROLE ii_ingest")
        # Insert is allowed: a restatement is a new row.
        cur.execute(
            "INSERT INTO financial_facts (instrument_id, basis, fiscal_year, "
            "period_type, line_item, period_start, period_end, value, "
            "known_from, filing_id, source_id) VALUES "
            "(%s, 'CONSOLIDATED', 2026, 'Q3', 'REVENUE', '2026-10-01', "
            "'2026-12-31', 1, now(), %s, %s)",
            (instrument, filing, source),
        )
        # Update is refused by privilege, before the trigger is even consulted.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("UPDATE financial_facts SET value = 2")
