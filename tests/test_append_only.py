"""Invariant 10, tested rather than trusted.

"Append-only enforced at the database level" is the Phase 6 exit criterion,
and it is the guarantee the whole product rests on: if a fact can be
overwritten, a point-in-time query is fiction.

Writing these tests is how the TRUNCATE hole was found. The triggers in
005 fire BEFORE UPDATE OR DELETE, and PostgreSQL does not route TRUNCATE
through DELETE triggers, so `TRUNCATE financial_facts` emptied the table and
reported success with enforcement fully in place. Migration 009 closed it.
The lesson is not about TRUNCATE. It is that an enforcement claim nobody has
attacked is a hope.
"""

from __future__ import annotations

from datetime import date

import psycopg
import pytest

from conftest import add_fact, add_filing, add_lifecycle, utc

# table -> a column that exists on it and is safe to write in a no-op update.
# Spelled out rather than discovered, because introspection picked identity
# columns, which cannot be updated for an unrelated reason and made the test
# pass for the wrong cause.
FACT_TABLES = {
    "instrument_identifiers": "known_from",
    "instrument_lifecycle": "known_from",
    "financial_facts": "known_from",
    "corporate_actions": "known_from",
    "filings": "filed_at",
}

MUTABLE_TABLES = {
    "ingestion_runs": "outcome",
    "tracked_instruments": "reason",
    "sources": "licence_note",
    "line_items": "label",
}


@pytest.fixture
def one_fact(conn, instrument, source):
    filing = add_filing(conn, instrument, source, period_end=date(2026, 9, 30),
                        filed_at=utc(2026, 10, 14), ref="only")
    add_fact(conn, instrument, source, filing, line_item="REVENUE", value=1000,
             fiscal_year=2026, period_type="Q2", known_from=utc(2026, 10, 14))
    return instrument


def test_update_on_a_fact_is_refused(conn, one_fact):
    with conn.cursor() as cur, pytest.raises(psycopg.errors.RaiseException) as exc:
        cur.execute("UPDATE financial_facts SET value = 1 WHERE instrument_id = %s",
                    (one_fact,))
    assert "append-only violation" in str(exc.value)


def test_delete_on_a_fact_is_refused(conn, one_fact):
    with conn.cursor() as cur, pytest.raises(psycopg.errors.RaiseException):
        cur.execute("DELETE FROM financial_facts WHERE instrument_id = %s", (one_fact,))


def test_truncate_on_a_fact_table_is_refused(conn, one_fact):
    """The hole migration 009 closed. Without it this passes silently."""
    with conn.cursor() as cur, pytest.raises(psycopg.errors.RaiseException):
        cur.execute("TRUNCATE financial_facts")


@pytest.mark.parametrize("table,column", sorted(FACT_TABLES.items()))
def test_every_fact_table_refuses_update_and_delete(conn, table, column):
    """Parameterised so a new fact table added without protection fails here."""
    for statement in (
        f"UPDATE {table} SET {column} = {column}",
        f"DELETE FROM {table}",
    ):
        with conn.cursor() as cur, pytest.raises(psycopg.errors.RaiseException):
            cur.execute(statement)
        conn.rollback()


@pytest.mark.parametrize("table", sorted(FACT_TABLES))
def test_every_fact_table_refuses_truncate(conn, table):
    """Truncation is blocked, but by two different mechanisms depending on the
    table, and it is worth being explicit about which.

    `filings` is referenced by a foreign key from `financial_facts`, so plain
    TRUNCATE is refused by PostgreSQL before the trigger is reached. The
    others hit the trigger from migration 009. Either is a refusal; asserting
    only one error type made this test fail for the wrong reason.

    The FK is not the protection we rely on — see the CASCADE test below.
    """
    with conn.cursor() as cur, pytest.raises(
        (psycopg.errors.RaiseException, psycopg.errors.FeatureNotSupported)
    ):
        cur.execute(f"TRUNCATE {table}")


def test_truncate_cascade_is_refused_by_the_trigger(conn):
    """The case the foreign key does not cover.

    `TRUNCATE filings CASCADE` satisfies the FK objection by truncating
    `financial_facts` too, so it is the route that would actually destroy
    history. Migration 009's trigger is what stops it, and this is the test
    that proves the trigger rather than the FK is doing the work.
    """
    with conn.cursor() as cur, pytest.raises(psycopg.errors.RaiseException) as exc:
        cur.execute("TRUNCATE filings CASCADE")
    assert "append-only violation" in str(exc.value)


@pytest.mark.parametrize("table,column", sorted(MUTABLE_TABLES.items()))
def test_operational_tables_remain_mutable(conn, table, column):
    """The exceptions are deliberate (005_append_only.sql).

    A table describing what a company reported is append-only. A table
    describing what we decided or what our pipeline did is not. Asserting this
    stops someone "fixing" the asymmetry later.
    """
    with conn.cursor() as cur:
        cur.execute(f"UPDATE {table} SET {column} = {column}")


# ---------------------------------------------------------------------------
# Constraints that encode Phase 3 discipline
# ---------------------------------------------------------------------------

def test_a_source_cannot_exist_without_a_licence_note(conn):
    """From 001_sources.sql. An adapter whose terms nobody read has no source
    row, and without a source row the foreign key refuses its facts. That is
    how the Phase 3 rule survives contact with a hurry."""
    with conn.cursor() as cur, pytest.raises(psycopg.errors.CheckViolation):
        cur.execute(
            "INSERT INTO sources (source_id, name, kind, licence_note, "
            "redistributable, verified_on) "
            "VALUES ('LAZY', 'Unchecked', 'FILINGS', '   ', true, %s)",
            (date(2026, 9, 11),),
        )


def test_a_failed_run_must_explain_itself(conn, source):
    with conn.cursor() as cur, pytest.raises(psycopg.errors.CheckViolation):
        cur.execute(
            "INSERT INTO ingestion_runs (source_id, kind, outcome, finished_at) "
            "VALUES (%s, 'FUNDAMENTALS', 'FAILED', now())",
            (source,),
        )


def test_a_ticker_alone_cannot_identify_an_instrument(conn, source):
    """Architecture §2.1. Two different companies may hold the same ticker at
    different times, so the identifier table is keyed on instrument and time,
    never on the string."""
    with conn.cursor() as cur:
        cur.execute("INSERT INTO instruments (isin) VALUES ('INE000AAAA01') "
                    "RETURNING instrument_id")
        first = cur.fetchone()[0]
        cur.execute("INSERT INTO instruments (isin) VALUES ('INE000BBBB01') "
                    "RETURNING instrument_id")
        second = cur.fetchone()[0]

    for inst, when in ((first, date(2010, 1, 1)), (second, date(2022, 1, 1))):
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO instrument_identifiers (instrument_id, exchange, "
                "ticker, company_name, effective_from, known_from, source_id) "
                "VALUES (%s, 'NSE', 'REUSED', 'Whoever', %s, %s, %s)",
                (inst, when, utc(when.year, 1, 1), source),
            )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(DISTINCT instrument_id) FROM instrument_identifiers "
            "WHERE ticker = 'REUSED'"
        )
        assert cur.fetchone()[0] == 2
