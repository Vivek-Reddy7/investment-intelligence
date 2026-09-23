"""Run log for the local-only analytics pipeline.

`ingestion_runs` (migration 001, watched by Phase 8/16's `ingestion_health()`
and Phase 22's operational-alerts detector) has covered the EDGAR
fundamentals job since the beginning. It has never covered `make technicals`,
`make risk`, `make combined`, `make sectors`, `make marketcap` or
`make backtest` -- those commands were built straight onto `connect()` /
`conn.commit()` with no run record at all, so "did last night's backtest
finish, and how long did it take" had no answer but re-reading the report and
guessing from whether the numbers look current.

`analytics_runs` (migration 031) is the parallel record for that pipeline,
kept as a separate table rather than bent into `ingestion_runs`'s
source-fetch shape -- see the migration's own header for why. This module is
the same start/finish bookkeeping `ingest/incremental.py` and
`ingest/backfill.py` already hand-write, pulled out once so six call sites
don't each reimplement it slightly differently.

`tracked_run` is a context manager rather than six manual start/finish call
pairs: it logs start and finish (or failure) through
`observability.logging`'s run-correlated structured lines, using the SAME
`run_context` every ingestion run already uses, and it writes the FAILED row
before re-raising -- a crashed analytics command is exactly the case a run
log exists to catch, so a bug in it must not also erase the evidence that it
happened.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date
from typing import Any, Iterator

import psycopg

from investment_intelligence.observability import logging as structured

log = logging.getLogger(__name__)

JOBS = ("TECHNICALS", "RISK", "COMBINED_SCORE", "SECTORS", "MARKET_CAP", "BACKTEST")


def _start_run(conn: psycopg.Connection, job: str, as_of: date | None) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO analytics_runs (job, as_of) VALUES (%s, %s) RETURNING run_id",
            (job, as_of),
        )
        return cur.fetchone()[0]


def _finish_run(conn: psycopg.Connection, run_id: int, outcome: str,
                rows_written: int, error: str | None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE analytics_runs
            SET    finished_at = now(), outcome = %s, rows_written = %s, error = %s
            WHERE  run_id = %s
            """,
            (outcome, rows_written, error, run_id),
        )


@contextmanager
def tracked_run(conn: psycopg.Connection, job: str, *,
                as_of: date | None = None) -> Iterator[dict[str, Any]]:
    """Wrap one analytics command's body. Yields a mutable dict; set
    `result["rows_written"]` before the block ends (default 0 if the job
    never sets it -- an honest "wrote nothing" rather than a crash).

    Commits the start row immediately, same as `incremental.run` does,
    so a run that crashes mid-way still leaves a RUNNING row behind rather
    than never having existed at all -- `ingestion_health()`'s
    NEVER_SUCCEEDED-vs-NEVER_RAN distinction (Phase 8) exists for exactly
    that failure mode, and a future job-health view over this table gets
    the same distinction for free only if RUNNING rows survive a crash.
    """
    run_id = _start_run(conn, job, as_of)
    conn.commit()
    result: dict[str, Any] = {"rows_written": 0}

    with structured.run_context(run_id, job=job):
        structured.info(log, f"{job.lower()} run started",
                        as_of=as_of.isoformat() if as_of else None)
        try:
            yield result
        except Exception as exc:
            conn.rollback()
            structured.error(log, f"{job.lower()} run failed",
                             error_type=type(exc).__name__, reason=str(exc))
            _finish_run(conn, run_id, "FAILED", result["rows_written"],
                       f"{type(exc).__name__}: {exc}")
            conn.commit()
            raise
        else:
            structured.info(log, f"{job.lower()} run finished",
                            rows_written=result["rows_written"])
            _finish_run(conn, run_id, "SUCCESS", result["rows_written"], None)
            conn.commit()


def recent(conn: psycopg.Connection, *, job: str | None = None, limit: int = 10) -> list[dict]:
    """The last `limit` runs, most recent first -- one job or every job,
    for `cli.py`'s `analytics-status` command and the local report's
    pipeline-history panel."""
    with conn.cursor() as cur:
        if job is None:
            cur.execute(
                """
                SELECT run_id, job, as_of, started_at, finished_at, outcome,
                       rows_written, error
                FROM   analytics_runs
                ORDER  BY started_at DESC LIMIT %s
                """,
                (limit,),
            )
        else:
            cur.execute(
                """
                SELECT run_id, job, as_of, started_at, finished_at, outcome,
                       rows_written, error
                FROM   analytics_runs
                WHERE  job = %s
                ORDER  BY started_at DESC LIMIT %s
                """,
                (job, limit),
            )
        columns = [d.name for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def last_success(conn: psycopg.Connection) -> dict[str, dict | None]:
    """The most recent SUCCESS row per job -- "when did this last actually
    work", not "when did it last try". One dict keyed by job name, every
    job present even if it has never once succeeded (value None), so a
    caller can render a fixed set of rows rather than only the jobs that
    happen to have a history."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (job) job, run_id, as_of, started_at,
                   finished_at, rows_written
            FROM   analytics_runs
            WHERE  outcome = 'SUCCESS'
            ORDER  BY job, started_at DESC
            """
        )
        columns = [d.name for d in cur.description]
        rows = {row[0]: dict(zip(columns, row)) for row in cur.fetchall()}
    return {j: rows.get(j) for j in JOBS}
