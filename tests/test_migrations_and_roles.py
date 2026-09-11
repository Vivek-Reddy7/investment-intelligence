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
from test_append_only import FACT_TABLES
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


# The only tables any role may delete from. Both are DERIVED and
# recomputable: `metric_values` is `metrics_as_of(now())` materialised, and
# replacing it wholesale is how a refresh works.
#
# An earlier version of this test asserted DELETE was granted nowhere at all,
# which was the wrong rule stated too confidently. The right rule is the
# distinction 005_append_only.sql already draws: a table recording what a
# company REPORTED is immutable; a table recording what we DERIVED or what our
# pipeline did is not. Deleting a derived row loses nothing, because it can be
# recomputed from the facts. Deleting a fact loses history permanently.
#
# Keeping this as an explicit allow-list rather than dropping the test means a
# future migration that grants DELETE on a fact table fails here.
# `quality_findings` joins the list for the same reason `metric_values` is on
# it: every row is recomputable by calling quality_as_of(), so replacing the
# table wholesale loses nothing.
#
# `ingestion_rejections` deliberately does NOT, and migration 019 revokes the
# DELETE that 018 granted out of habit. A rejection is not derivable from the
# facts -- the rejected items are precisely the ones that never became facts --
# so deleting one destroys the only evidence we saw that data and declined it.
# This test is what forced that distinction to be made explicit.
DELETABLE_TABLES = {"metric_values", "quality_findings"}


@pytest.mark.parametrize("role", ["ii_app", "ii_ingest"])
def test_delete_is_granted_only_on_derived_tables(conn, role):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name FROM information_schema.role_table_grants
            WHERE  grantee = %s AND privilege_type = 'DELETE'
            """,
            (role,),
        )
        granted = {row[0] for row in cur.fetchall()}
    assert granted <= DELETABLE_TABLES, (
        f"DELETE granted on non-derived table(s): {sorted(granted - DELETABLE_TABLES)}"
    )


def test_no_application_role_can_delete_a_fact(conn):
    """The rule that actually matters, stated directly.

    Scoped to the application roles, because a table's OWNER implicitly holds
    every privilege in PostgreSQL and cannot be stripped of them. That is not a
    hole, it is the reason invariant 10 is enforced twice: privileges stop the
    application roles, and the triggers in 005/009 stop everyone including the
    owner. Each layer covers the other's blind spot.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT grantee, table_name, privilege_type
            FROM   information_schema.role_table_grants
            WHERE  grantee IN ('ii_app', 'ii_ingest')
              AND  privilege_type IN ('DELETE', 'TRUNCATE')
              AND  table_name = ANY(%s)
            """,
            (list(FACT_TABLES),),
        )
        assert cur.fetchall() == []


def test_the_owner_can_delete_but_the_trigger_stops_it(conn, seeded):
    """Proves the two layers are genuinely complementary rather than redundant.

    The test harness connects as the owner, so privileges do not protect us
    here at all -- and the delete is still refused, by the trigger.
    """
    with conn.cursor() as cur, pytest.raises(psycopg.errors.RaiseException):
        cur.execute("DELETE FROM financial_facts")


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
