"""Writing facts, idempotently and without losing restatements.

This module exists for one rule, from ADR 004 and architecture §2.12:

    Re-ingesting the SAME value must not create a second version.
    Ingesting a GENUINELY CHANGED value must create one.

Both halves matter and they fail in opposite directions:

- Too permissive, and every daily run appends a meaningless version. After a
  year the store is 365x larger and `facts_as_of` has to sort through noise to
  answer anything.
- Too strict, and a restatement is silently dropped. That is worse, and it is
  the failure the whole product exists to prevent -- the number on screen stays
  confidently wrong and no error is raised anywhere.

The check cannot be a UNIQUE constraint over the value. 1000 -> 900 -> 1000 is
a real sequence (a correction later withdrawn), and a constraint including the
value would reject the third version. So the rule is implemented as a
conditional insert against the current latest version.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import psycopg

from investment_intelligence.sources.base import FilingDocument, ReportedFact


@dataclass
class WriteResult:
    facts_written: int = 0
    facts_unchanged: int = 0
    filings_written: int = 0
    filings_seen_before: int = 0

    def __add__(self, other: WriteResult) -> WriteResult:
        return WriteResult(
            self.facts_written + other.facts_written,
            self.facts_unchanged + other.facts_unchanged,
            self.filings_written + other.filings_written,
            self.filings_seen_before + other.filings_seen_before,
        )


class UnknownInstrument(KeyError):
    """An identifier we do not track. Not an error to swallow silently."""


def resolve_ref(conn: psycopg.Connection, scheme: str, value: str) -> int:
    """Map a source's own identifier to our internal instrument_id.

    Architecture §2.1: nothing downstream ever sees a provider's key. The
    mapping is a stored, sourced fact (`instrument_external_ids`) rather than
    a string transformation, because guessing at identity is how two
    companies' histories get merged.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT instrument_id FROM instrument_external_ids "
            "WHERE scheme = %s AND value = %s",
            (scheme, value),
        )
        row = cur.fetchone()
    if row is None:
        raise UnknownInstrument(f"{scheme}={value}")
    return row[0]


def _upsert_filing(
    conn: psycopg.Connection,
    instrument_id: int,
    doc: FilingDocument,
    source_id: str,
    run_id: int | None,
) -> tuple[int, bool]:
    """Returns (filing_id, was_new).

    Filings carry a UNIQUE on (source_id, source_ref, content_hash), so the
    same document re-fetched resolves to the same row. A RESTATED document has
    a different content_hash and is therefore a new filing -- which is correct:
    it is a different document making different claims.

    `filings` is append-only, so ON CONFLICT DO NOTHING is the only available
    upsert. DO UPDATE would be refused by the trigger, which is the point.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO filings (instrument_id, filing_type, period_end, filed_at,
                                 retrieved_at, source_id, run_id, source_ref,
                                 content_hash)
            VALUES (%s, %s, %s, %s, now(), %s, %s, %s, %s)
            ON CONFLICT (source_id, source_ref, content_hash) DO NOTHING
            RETURNING filing_id
            """,
            (instrument_id, doc.filing_type, doc.period_end, doc.filed_at,
             source_id, run_id, doc.source_ref, doc.content_hash),
        )
        row = cur.fetchone()
        if row is not None:
            return row[0], True

        cur.execute(
            "SELECT filing_id FROM filings "
            "WHERE source_id = %s AND source_ref = %s AND content_hash = %s",
            (source_id, doc.source_ref, doc.content_hash),
        )
        return cur.fetchone()[0], False


# The conditional insert that is the whole point of this module.
#
# Inserts only when the latest known version of this fact identity differs
# from the incoming value (or when there is no version yet). `IS DISTINCT
# FROM` rather than `<>` so that a NULL-free comparison also handles the
# no-previous-row case without a second query.
_INSERT_FACT = """
WITH latest AS (
    SELECT value
    FROM   financial_facts
    WHERE  instrument_id = %(instrument_id)s
      AND  basis         = %(basis)s
      AND  fiscal_year   = %(fiscal_year)s
      AND  period_type   = %(period_type)s
      AND  line_item     = %(line_item)s
      AND  currency      = %(currency)s
    ORDER  BY known_from DESC
    LIMIT  1
)
INSERT INTO financial_facts
    (instrument_id, basis, fiscal_year, period_type, line_item,
     period_start, period_end, value, currency, known_from,
     filing_id, source_id, run_id)
SELECT %(instrument_id)s, %(basis)s, %(fiscal_year)s, %(period_type)s,
       %(line_item)s, %(period_start)s, %(period_end)s, %(value)s,
       %(currency)s, %(known_from)s, %(filing_id)s, %(source_id)s, %(run_id)s
WHERE NOT EXISTS (
    SELECT 1 FROM latest WHERE latest.value IS NOT DISTINCT FROM %(value)s
)
RETURNING fact_id
"""


def write_fact(
    conn: psycopg.Connection,
    instrument_id: int,
    fact: ReportedFact,
    *,
    filing_id: int,
    source_id: str,
    known_from: datetime,
    run_id: int | None = None,
) -> bool:
    """Write one fact if it says something new. Returns True if a row was added."""
    with conn.cursor() as cur:
        cur.execute(_INSERT_FACT, {
            "instrument_id": instrument_id,
            "basis": fact.basis,
            "fiscal_year": fact.fiscal_year,
            "period_type": fact.period_type,
            "line_item": fact.line_item,
            "period_start": fact.period_start,
            "period_end": fact.period_end,
            "value": fact.value,
            "currency": fact.currency,
            "known_from": known_from,
            "filing_id": filing_id,
            "source_id": source_id,
            "run_id": run_id,
        })
        return cur.fetchone() is not None


def write_filing(
    conn: psycopg.Connection,
    doc: FilingDocument,
    *,
    source_id: str,
    key_scheme: str,
    known_from: datetime | None = None,
    run_id: int | None = None,
) -> WriteResult:
    """Write a filing and its facts. Safe to call repeatedly with the same input.

    `known_from` defaults to the filing's own `filed_at`, and that default is
    the important part.

    Transaction time means "when could this have been known", not "when did we
    happen to look". EDGAR tells us the date each claim entered the public
    record, so using it makes a point-in-time query truthful. An earlier
    version stamped every document in a run with the ingestion timestamp
    instead, which collapsed the transaction-time axis: FY2018 revenue as
    reported in 2019 and as restated in 2021 both became "known from today",
    so they were the same fact at the same instant and the second one
    collided. Had the unique constraint not caught it, the wedge would have
    been silently gone -- every historical query would have returned today's
    view.

    The override exists only for sources that genuinely cannot date their own
    claims, where ingestion time is the best available answer and should be
    recorded as such.
    """
    known_from = known_from or doc.filed_at
    instrument_id = resolve_ref(conn, key_scheme, doc.instrument_ref)
    filing_id, was_new = _upsert_filing(conn, instrument_id, doc, source_id, run_id)

    result = WriteResult(
        filings_written=1 if was_new else 0,
        filings_seen_before=0 if was_new else 1,
    )

    for fact in doc.facts:
        if write_fact(conn, instrument_id, fact, filing_id=filing_id,
                      source_id=source_id, known_from=known_from, run_id=run_id):
            result.facts_written += 1
        else:
            result.facts_unchanged += 1

    return result
