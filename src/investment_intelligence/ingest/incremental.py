"""The job that runs forever unattended.

Different from backfill in every way that matters, which is why they are
separate phases and separate modules:

| | backfill (phase 7) | incremental (this) |
|---|---|---|
| runs for | hours | seconds |
| window | ten years | recent |
| interruption | expected, checkpointed | just retry next cycle |
| failure mode | partial load | silent staleness |

That last row is the point. A backfill that half-finishes is visible: the
checkpoint table says so. An incremental job that stops running produces **no
evidence at all** — the site keeps serving yesterday's numbers, confidently,
and every page still renders. Which is why this module's real output is not
the facts it writes but the `ingestion_runs` row it always writes, and why
`ingestion_health()` exists to notice the absence of one.

The correctness property, from ADR 004: re-ingesting an unchanged value
creates no new version, while a genuinely changed value does. That is
`writer.write_filing`'s job, and it is what makes running this every day
cheap instead of quadratic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import psycopg

from investment_intelligence.ingest.writer import (
    UnknownInstrument,
    WriteResult,
    write_filing,
)
from investment_intelligence.ingest import rejections as rejection_log
from investment_intelligence.observability import logging as structured
from investment_intelligence.sources.base import FilingSource, Rejection

log = logging.getLogger(__name__)


@dataclass
class IncrementalReport:
    run_id: int | None = None
    instruments_checked: int = 0
    instruments_failed: int = 0
    new_filings: int = 0
    writes: WriteResult = field(default_factory=WriteResult)
    rejections: list[Rejection] = field(default_factory=list)
    metrics_refreshed: int = 0

    @property
    def outcome(self) -> str:
        if self.instruments_failed and self.instruments_checked > self.instruments_failed:
            return "PARTIAL"
        if self.instruments_failed and self.instruments_checked == self.instruments_failed:
            return "FAILED"
        return "SUCCESS"


def tracked(conn: psycopg.Connection, scheme: str) -> list[tuple[int, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.instrument_id, x.value
            FROM   tracked_instruments t
            JOIN   instrument_external_ids x
                     ON x.instrument_id = t.instrument_id AND x.scheme = %s
            WHERE  t.removed_on IS NULL
            ORDER  BY t.instrument_id
            """,
            (scheme,),
        )
        return cur.fetchall()


def known_filing_refs(conn: psycopg.Connection, source_id: str) -> set[str]:
    """Accession numbers we have already parsed, for this source.

    Cheap enough to load wholesale at this scale, and it turns the job from
    "re-parse everything" into "notice what is new". `content_hash` is the
    accession number for EDGAR, and a restated document has a different one --
    so a restatement correctly reads as a new filing rather than a duplicate.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT content_hash FROM filings WHERE source_id = %s", (source_id,)
        )
        return {row[0] for row in cur.fetchall()}


def _start_run(conn: psycopg.Connection, source_id: str,
               since: date, until: date) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingestion_runs (source_id, kind, window_start, window_end)
            VALUES (%s, 'FUNDAMENTALS', %s, %s) RETURNING run_id
            """,
            (source_id, since, until),
        )
        return cur.fetchone()[0]


def _finish_run(conn: psycopg.Connection, report: IncrementalReport) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingestion_runs
            SET    finished_at = now(), outcome = %s,
                   rows_written = %s, rows_rejected = %s, error = %s
            WHERE  run_id = %s
            """,
            (report.outcome, report.writes.facts_written, len(report.rejections),
             f"{report.instruments_failed} instrument(s) failed"
             if report.instruments_failed else None,
             report.run_id),
        )


def run(
    conn: psycopg.Connection,
    source: FilingSource,
    *,
    lookback_years: int = 2,
    limiter=None,
    refresh_metrics: bool = True,
) -> IncrementalReport:
    """One incremental cycle over every tracked instrument.

    `lookback_years` bounds how far back we look for *newly filed* documents.
    Two years rather than one because a restatement can revise a period well
    after it closed -- the Sify restatement found in Phase 7 arrived eighteen
    months late. Narrowing this to save time would silently stop catching the
    thing the product exists for.
    """
    report = IncrementalReport()
    until = date.today() + timedelta(days=1)
    since = date(until.year - lookback_years, 1, 1)

    report.run_id = _start_run(conn, source.source_id, since, until)
    conn.commit()

    already_seen = known_filing_refs(conn, source.source_id)

    # Every line below carries this run id, so "what happened in run 47" is a
    # single grep rather than a reconstruction from timestamps.
    with structured.run_context(report.run_id, source_id=source.source_id,
                                job="incremental"):
        structured.info(log, "incremental cycle started",
                        window_start=str(since), window_end=str(until),
                        known_filings=len(already_seen))
        _run_cycle(conn, source, report, already_seen, since, until, limiter)

        if refresh_metrics:
            with conn.cursor() as cur:
                cur.execute("SELECT refresh_metric_values()")
                report.metrics_refreshed = cur.fetchone()[0]

        structured.info(log, "incremental cycle finished",
                        outcome=report.outcome,
                        instruments_checked=report.instruments_checked,
                        instruments_failed=report.instruments_failed,
                        new_filings=report.new_filings,
                        facts_written=report.writes.facts_written,
                        rejections=len(report.rejections),
                        metrics_refreshed=report.metrics_refreshed)

    rejection_log.record(conn, report.run_id, source.source_id, report.rejections)
    _finish_run(conn, report)
    conn.commit()
    return report


def _run_cycle(conn, source, report, already_seen, since, until, limiter) -> None:
    """The per-instrument loop, split out so `run` reads as a sequence.

    Extracted when structured logging was threaded through: the wrapping
    context manager plus the loop plus the teardown made one function that had
    to be read three times to follow.
    """
    for instrument_id, ref in tracked(conn, source.key_scheme):
        report.instruments_checked += 1
        try:
            if limiter is not None:
                limiter.wait()

            for item in source.fetch_filings(ref, since, until):
                if isinstance(item, Rejection):
                    report.rejections.append(item)
                    continue
                if item.content_hash in already_seen:
                    # Already parsed. Skipping here rather than relying on the
                    # writer's dedup keeps the daily job proportional to what
                    # is NEW instead of to the whole history.
                    continue

                report.new_filings += 1
                report.writes = report.writes + write_filing(
                    conn, item, source_id=source.source_id,
                    key_scheme=source.key_scheme, run_id=report.run_id,
                )
                already_seen.add(item.content_hash)

            conn.commit()

        except UnknownInstrument as exc:
            conn.rollback()
            structured.warning(log, "unknown instrument", instrument_ref=ref,
                               reason=str(exc))
            report.instruments_failed += 1

        except Exception as exc:  # noqa: BLE001 - one company must not end the cycle
            conn.rollback()
            structured.warning(log, "instrument failed", instrument_ref=ref,
                               error_type=type(exc).__name__, reason=str(exc))
            report.instruments_failed += 1


def health(conn: psycopg.Connection) -> list[dict]:
    """Ingestion health, including the jobs that did NOT run.

    See migration 015. This is the answer to "scheduler never fired", which a
    run-log query cannot give: there is no row to find.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM ingestion_health()")
        columns = [d.name for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def unhealthy(conn: psycopg.Connection) -> list[dict]:
    return [row for row in health(conn) if row["status"] != "OK"]
