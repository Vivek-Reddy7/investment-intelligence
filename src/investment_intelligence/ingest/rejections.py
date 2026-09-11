"""Persisting rejections, and classifying them.

Phase 7 rejected 372 items and recorded only the count. The reasons lived in a
report object that went out of scope, so we knew we were discarding data and
not what or why.

Classification is the point. Individual reasons carry values and dates -- "a
90-day span", "as at 2017-12-31" -- so grouping on the raw text produces 372
groups of one. The CLASS is what turns a count into a decision: is this data we
should be parsing, or data that genuinely does not belong to us?
"""

from __future__ import annotations

import re

import psycopg

from investment_intelligence.sources.base import Rejection

# Ordered: first match wins, so put the specific before the general.
# Deliberately a small, readable list rather than a clever generic normaliser.
# A wrong class silently merges two problems into one line of a report.
_CLASSES: list[tuple[str, re.Pattern[str]]] = [
    # A deliberate decision, not a parser gap. Listed first because the YTD
    # reason also mentions periods and would otherwise fall to a later rule.
    ("YTD_NOT_STORED",           re.compile(r"year-to-date period not stored")),
    ("UNPLACEABLE_BALANCE_DATE", re.compile(r"balance date matches no reported period")),
    ("UNRECOGNISED_PERIOD_SPAN", re.compile(r"unrecognised period span")),
    ("AMBIGUOUS_QUARTER",        re.compile(r"cannot say which quarter", re.I)),
    ("DUPLICATE_IN_FILING",      re.compile(r"reported twice in one filing")),
    ("NOT_A_REGISTRANT",         re.compile(r"not a known SEC registrant")),
    ("UNKNOWN_REFERENCE",        re.compile(r"unknown reference")),
    ("SOURCE_UNAVAILABLE",       re.compile(r"companyfacts unavailable|HTTP \d{3}")),
    ("INVALID_VALUE",            re.compile(r"must be Decimal|is NaN|must be a 3-letter")),
    ("INVALID_PERIOD",           re.compile(r"ends before it starts|should span|implausible")),
]


def classify(reason: str) -> str:
    for name, pattern in _CLASSES:
        if pattern.search(reason):
            return name
    return "UNCLASSIFIED"


def record(
    conn: psycopg.Connection,
    run_id: int,
    source_id: str,
    rejections: list[Rejection],
) -> int:
    """Persist a run's rejections. Returns the number stored."""
    if not rejections:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO ingestion_rejections
                (run_id, source_id, instrument_ref, detail, reason, reason_class)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            [(run_id, source_id, r.instrument_ref, r.detail, r.reason,
              classify(r.reason)) for r in rejections],
        )
    return len(rejections)


def summary(conn: psycopg.Connection) -> list[dict]:
    """Rejections by class: what we are throwing away, and how much of it."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT reason_class,
                   count(*)                        AS rejections,
                   count(DISTINCT instrument_ref)  AS instruments,
                   min(reason)                     AS example
            FROM   ingestion_rejections
            GROUP  BY reason_class
            ORDER  BY count(*) DESC
            """
        )
        columns = [d.name for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def unclassified(conn: psycopg.Connection) -> list[str]:
    """Reasons no rule matched.

    A growing UNCLASSIFIED bucket means the source started rejecting things
    for a new reason and nobody noticed -- so this is checked rather than
    assumed empty.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT reason FROM ingestion_rejections "
            "WHERE reason_class = 'UNCLASSIFIED' LIMIT 20")
        return [row[0] for row in cur.fetchall()]
