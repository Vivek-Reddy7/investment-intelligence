"""Resumable, rate-limited historical backfill.

Phase 7's whole difficulty. A ten-year load over ~500 companies runs for hours
against a provider that will rate-limit us, and it will be interrupted: a
dropped connection, a ban, GitHub Actions hitting its job ceiling, a laptop
closing. Restarting from zero is not acceptable, because each restart spends
another several hours of somebody else's quota.

Three properties, in order of importance:

1. **Resumable.** Progress is checkpointed per instrument. A resumed run skips
   DONE and retries FAILED. Nothing re-fetches work already completed.

2. **Idempotent.** Even mid-instrument interruption is safe, because fact
   writes only add a row when the value actually changed (see writer.py). So a
   retry cannot double-count, and we do not need to know how far through an
   instrument the crash happened.

3. **Polite.** A fixed minimum interval between requests. Getting banned
   costs more than going slowly, and a backfill has no deadline.

Every run writes an `ingestion_runs` row, including when it fails -- a failed
run is a row, not an absence, or a scheduler that never fired is
indistinguishable from a run with nothing to do.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import psycopg

from investment_intelligence.ingest.writer import (
    UnknownInstrument,
    WriteResult,
    write_filing,
)
from investment_intelligence.sources.base import FilingSource, Rejection

log = logging.getLogger(__name__)


class RateLimiter:
    """Minimum interval between calls. Deliberately simple.

    A token bucket would allow bursts, and bursts are what get an IP banned.
    A backfill is not latency-sensitive, so the boring choice is the right one.
    """

    def __init__(self, min_interval_seconds: float, *, sleep=time.sleep,
                 clock=time.monotonic) -> None:
        self.min_interval = min_interval_seconds
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            remaining = self.min_interval - (now - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()


@dataclass
class BackfillReport:
    job: str
    run_id: int | None = None
    instruments_done: int = 0
    instruments_skipped: int = 0
    instruments_failed: int = 0
    writes: WriteResult = field(default_factory=WriteResult)
    rejections: list[Rejection] = field(default_factory=list)

    @property
    def outcome(self) -> str:
        if self.instruments_failed and (self.instruments_done or self.instruments_skipped):
            return "PARTIAL"
        if self.instruments_failed:
            return "FAILED"
        return "SUCCESS"


def plan(conn: psycopg.Connection, job: str) -> int:
    """Create PENDING checkpoints for every tracked instrument. Idempotent.

    Called before the first run and safe to call again: a checkpoint that
    already exists is left alone, so re-planning never resets progress.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO backfill_checkpoints (job, instrument_id)
            SELECT %s, instrument_id
            FROM   tracked_instruments
            WHERE  removed_on IS NULL
            ON CONFLICT (job, instrument_id) DO NOTHING
            """,
            (job,),
        )
        return cur.rowcount


def outstanding(conn: psycopg.Connection, job: str, scheme: str) -> list[tuple[int, str]]:
    """Work still to do, as (instrument_id, external ref in `scheme`).

    IN_PROGRESS is included: it means a previous run died mid-instrument, and
    retrying is safe because the writes are idempotent. Trusting an
    IN_PROGRESS checkpoint would silently skip a partially loaded company.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.instrument_id, x.value
            FROM   backfill_checkpoints c
            JOIN   instrument_external_ids x
                     ON x.instrument_id = c.instrument_id AND x.scheme = %s
            WHERE  c.job = %s
              AND  c.state IN ('PENDING', 'IN_PROGRESS', 'FAILED')
            ORDER  BY c.attempts, c.instrument_id
            """,
            (scheme, job),
        )
        return cur.fetchall()


def _mark(conn: psycopg.Connection, job: str, instrument_id: int, state: str,
          *, written: int = 0, rejected: int = 0, error: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE backfill_checkpoints
            SET    state = %s,
                   attempts = attempts + 1,
                   facts_written = facts_written + %s,
                   facts_rejected = facts_rejected + %s,
                   last_error = %s,
                   updated_at = now()
            WHERE  job = %s AND instrument_id = %s
            """,
            (state, written, rejected, error, job, instrument_id),
        )


def _start_run(conn: psycopg.Connection, source_id: str,
               since: date, until: date) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingestion_runs (source_id, kind, window_start, window_end)
            VALUES (%s, 'FUNDAMENTALS', %s, %s)
            RETURNING run_id
            """,
            (source_id, since, until),
        )
        return cur.fetchone()[0]


def _finish_run(conn: psycopg.Connection, run_id: int, report: BackfillReport) -> None:
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
             run_id),
        )


def run(
    conn: psycopg.Connection,
    source: FilingSource,
    *,
    job: str,
    since: date,
    until: date,
    limiter: RateLimiter | None = None,
    max_instruments: int | None = None,
) -> BackfillReport:
    """Backfill outstanding instruments. Resume by calling again.

    `max_instruments` bounds one invocation, which is how this survives a
    scheduler with a job time limit: run a slice, commit, exit cleanly, and
    the next invocation picks up where it stopped.
    """
    limiter = limiter or RateLimiter(0.0)
    report = BackfillReport(job=job)

    plan(conn, job)
    work = outstanding(conn, job, source.key_scheme)
    if max_instruments is not None:
        work = work[:max_instruments]

    report.run_id = _start_run(conn, source.source_id, since, until)
    conn.commit()

    for instrument_id, ref in work:
        _mark(conn, job, instrument_id, "IN_PROGRESS")
        conn.commit()

        written = 0
        rejected: list[Rejection] = []
        try:
            limiter.wait()
            saw_document = False

            for item in source.fetch_filings(ref, since, until):
                if isinstance(item, Rejection):
                    rejected.append(item)
                    continue
                saw_document = True
                # No known_from: the writer uses the filing's own filed_at,
                # which is the real transaction time. See writer.write_filing.
                result = write_filing(
                    conn, item, source_id=source.source_id,
                    key_scheme=source.key_scheme, run_id=report.run_id,
                )
                report.writes = report.writes + result
                written += result.facts_written

            # SKIPPED, not DONE: nothing existed to load. Keeping these
            # distinct is what lets gap reporting tell "loaded" apart from
            # "there was nothing", instead of both looking like success.
            state = "DONE" if saw_document else "SKIPPED"
            _mark(conn, job, instrument_id, state,
                  written=written, rejected=len(rejected))
            report.rejections.extend(rejected)
            if state == "DONE":
                report.instruments_done += 1
            else:
                report.instruments_skipped += 1
            conn.commit()

        except UnknownInstrument as exc:
            conn.rollback()
            _mark(conn, job, instrument_id, "FAILED", error=f"unknown instrument: {exc}")
            report.instruments_failed += 1
            conn.commit()

        except Exception as exc:  # noqa: BLE001 - one company must not end the run
            # Deliberately broad. A malformed document or a provider hiccup on
            # one company must not abandon the other 499; the checkpoint
            # records the failure so a later run retries just this one.
            conn.rollback()
            log.warning("backfill failed for %s: %s", ref, exc)
            _mark(conn, job, instrument_id, "FAILED", error=f"{type(exc).__name__}: {exc}")
            report.instruments_failed += 1
            conn.commit()

    _finish_run(conn, report.run_id, report)
    conn.commit()
    return report


def gaps(conn: psycopg.Connection, job: str, scheme: str = 'SEC_CIK') -> dict[str, list[str]]:
    """What the backfill did not load, enumerated rather than hidden.

    The Phase 7 exit criterion says gaps are enumerated, not hidden. A
    backfill that quietly loaded 430 of 500 companies and reported success is
    the failure mode here: every screen afterwards is missing 14% of the
    universe and nothing looks wrong.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.state, coalesce(i.isin, x.value), c.last_error
            FROM   backfill_checkpoints c
            JOIN   instruments i USING (instrument_id)
            LEFT   JOIN instrument_external_ids x
                     ON x.instrument_id = c.instrument_id
                    AND x.scheme = %s
            WHERE  c.job = %s AND c.state <> 'DONE'
            ORDER  BY c.state, 2
            """,
            (scheme, job),
        )
        out: dict[str, list[str]] = {}
        for state, ref, error in cur.fetchall():
            out.setdefault(state, []).append(ref if not error else f"{ref} ({error})")
        return out
