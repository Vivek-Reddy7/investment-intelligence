"""Test fixtures.

Every test runs inside a transaction that is rolled back afterwards. That is
not just tidiness: the fact tables refuse DELETE and TRUNCATE (invariant 10),
so rollback is the only cleanup available. It also means tests exercise the
same write path the real ingestion uses, privileges and triggers included.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone

import psycopg
import pytest

from investment_intelligence.db import connect, migrate

TEST_DSN = os.environ.get("TEST_DATABASE_URL", "postgresql:///ii_test")


def utc(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def migrated_db() -> str:
    """Apply migrations once against the test database."""
    with connect(TEST_DSN) as conn:
        migrate(conn)
        conn.commit()
    return TEST_DSN


@pytest.fixture
def conn(migrated_db: str):
    """A connection whose work is always rolled back."""
    connection = psycopg.connect(migrated_db)
    connection.autocommit = False
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


@pytest.fixture
def source(conn) -> str:
    """A source row. Required by every fact, by design (see 001_sources.sql)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sources (source_id, name, kind, licence_note,
                                 redistributable, verified_on)
            VALUES ('TEST_FILINGS', 'Test filings', 'FILINGS',
                    'Test fixture. Not a real licence position.', true, %s)
            ON CONFLICT (source_id) DO NOTHING
            """,
            (date(2026, 9, 11),),
        )
    return "TEST_FILINGS"


@pytest.fixture
def instrument(conn) -> int:
    """One instrument, listed 2015-04-01."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO instruments (isin) VALUES ('INE000TEST01') "
            "RETURNING instrument_id"
        )
        return cur.fetchone()[0]


def add_filing(
    conn,
    instrument_id: int,
    source_id: str,
    *,
    period_end: date,
    filed_at: datetime,
    ref: str,
    filing_type: str = "QUARTERLY_RESULT",
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO filings (instrument_id, filing_type, period_end,
                                 filed_at, retrieved_at, source_id,
                                 source_ref, content_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, md5(%s))
            RETURNING filing_id
            """,
            (instrument_id, filing_type, period_end, filed_at, filed_at,
             source_id, ref, ref),
        )
        return cur.fetchone()[0]


def add_fact(
    conn,
    instrument_id: int,
    source_id: str,
    filing_id: int,
    *,
    line_item: str,
    value: float,
    fiscal_year: int,
    period_type: str,
    known_from: datetime,
    basis: str = "CONSOLIDATED",
    period_start: date | None = None,
    period_end: date | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO financial_facts
                (instrument_id, basis, fiscal_year, period_type, line_item,
                 period_start, period_end, value, known_from, filing_id, source_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (instrument_id, basis, fiscal_year, period_type, line_item,
             period_start or date(fiscal_year, 4, 1),
             period_end or date(fiscal_year, 6, 30),
             value, known_from, filing_id, source_id),
        )


def add_lifecycle(
    conn,
    instrument_id: int,
    source_id: str,
    *,
    event: str,
    event_date: date,
    known_from: datetime,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO instrument_lifecycle
                (instrument_id, event, event_date, known_from, source_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (instrument_id, event, event_date, known_from, source_id),
        )
