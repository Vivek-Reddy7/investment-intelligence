"""The run log for the local-only analytics pipeline (migration 031).

`ingestion_runs` never covered `make technicals`/`risk`/`combined`/`sectors`/
`marketcap`/`backtest` -- see observability/analytics_runs.py's module
docstring for why this is a deliberately separate table rather than a bent
`ingestion_runs` row. These tests are the equivalent of test_observability.py
for THIS run log: a job that succeeds leaves a SUCCESS row behind with the
right counts, and a job that raises leaves a FAILED row behind rather than no
row at all.

Uses `committed_conn`, not the rollback-isolated `conn` -- `tracked_run`
commits internally, the same reason `ingest/backfill.py`'s and
`ingest/incremental.py`'s own tests use it (see conftest.py's docstring for
why rollback-only isolation cannot be used to test something whose entire
purpose is surviving a crash).
"""

from __future__ import annotations

from datetime import date

import pytest

from investment_intelligence.observability import analytics_runs


def test_tracked_run_records_success_with_rows_written(committed_conn):
    with analytics_runs.tracked_run(committed_conn, "TECHNICALS", as_of=date(2026, 1, 2)) as run:
        run["rows_written"] = 7

    rows = analytics_runs.recent(committed_conn, job="TECHNICALS")
    assert len(rows) == 1
    row = rows[0]
    assert row["outcome"] == "SUCCESS"
    assert row["rows_written"] == 7
    assert row["as_of"] == date(2026, 1, 2)
    assert row["finished_at"] is not None
    assert row["error"] is None


def test_tracked_run_defaults_rows_written_to_zero_when_never_set(committed_conn):
    """A job that runs and genuinely writes nothing (e.g. sectors against an
    empty tracked set) must record SUCCESS with 0, not crash on a missing
    key."""
    with analytics_runs.tracked_run(committed_conn, "SECTORS"):
        pass

    row = analytics_runs.recent(committed_conn, job="SECTORS")[0]
    assert row["outcome"] == "SUCCESS"
    assert row["rows_written"] == 0
    assert row["as_of"] is None  # SECTORS has no single as_of


def test_tracked_run_records_failure_and_reraises(committed_conn):
    """The exact failure mode this table exists to catch: a crashed
    analytics command must leave a FAILED row with the error, not silently
    vanish because the crash happened before any commit."""
    with pytest.raises(ValueError, match="boom"):
        with analytics_runs.tracked_run(committed_conn, "RISK", as_of=date(2026, 1, 1)) as run:
            run["rows_written"] = 3  # partial progress before the crash
            raise ValueError("boom")

    row = analytics_runs.recent(committed_conn, job="RISK")[0]
    assert row["outcome"] == "FAILED"
    assert row["rows_written"] == 3
    assert "boom" in row["error"]
    assert row["finished_at"] is not None


def test_tracked_run_rolls_back_uncommitted_work_before_recording_failure(committed_conn):
    """A crash mid-job may leave uncommitted writes on the connection. Those
    must be rolled back before the FAILED row is written, or the failed
    row's own INSERT would be committed alongside whatever the crashed job
    left dangling."""
    with committed_conn.cursor() as cur:
        cur.execute("INSERT INTO instruments (isin) VALUES ('INE000ARUN01') "
                     "RETURNING instrument_id")
        stray_id = cur.fetchone()[0]
    committed_conn.commit()

    with pytest.raises(RuntimeError):
        with analytics_runs.tracked_run(committed_conn, "COMBINED_SCORE") as run:
            with committed_conn.cursor() as cur:
                cur.execute("INSERT INTO instruments (isin) VALUES ('INE000ARUN02')")
            raise RuntimeError("crashed mid-job")

    with committed_conn.cursor() as cur:
        cur.execute("SELECT instrument_id FROM instruments WHERE isin = 'INE000ARUN02'")
        assert cur.fetchone() is None  # rolled back
    # The instrument inserted and committed BEFORE the tracked run started
    # is unaffected -- rollback only undoes the run's own uncommitted work.
    with committed_conn.cursor() as cur:
        cur.execute("SELECT instrument_id FROM instruments WHERE instrument_id = %s", (stray_id,))
        assert cur.fetchone() is not None


def test_recent_orders_most_recent_first_across_jobs(committed_conn):
    with analytics_runs.tracked_run(committed_conn, "TECHNICALS", as_of=date(2026, 1, 1)):
        pass
    with analytics_runs.tracked_run(committed_conn, "RISK", as_of=date(2026, 1, 1)):
        pass

    rows = analytics_runs.recent(committed_conn, limit=10)
    assert [r["job"] for r in rows[:2]] == ["RISK", "TECHNICALS"]


def test_recent_respects_limit(committed_conn):
    for _ in range(3):
        with analytics_runs.tracked_run(committed_conn, "TECHNICALS", as_of=date(2026, 1, 1)):
            pass
    assert len(analytics_runs.recent(committed_conn, job="TECHNICALS", limit=2)) == 2


def test_last_success_ignores_a_later_failed_run(committed_conn):
    """The most recent SUCCESS, not the most recent attempt -- a failed
    re-run must not erase the record of when the job last actually
    worked."""
    with analytics_runs.tracked_run(committed_conn, "MARKET_CAP", as_of=date(2026, 1, 1)) as run:
        run["rows_written"] = 5

    with pytest.raises(ValueError):
        with analytics_runs.tracked_run(committed_conn, "MARKET_CAP", as_of=date(2026, 1, 2)):
            raise ValueError("edgar down")

    last = analytics_runs.last_success(committed_conn)
    assert last["MARKET_CAP"]["as_of"] == date(2026, 1, 1)
    assert last["MARKET_CAP"]["rows_written"] == 5


def test_last_success_is_none_for_a_job_with_no_history(committed_conn):
    last = analytics_runs.last_success(committed_conn)
    assert last == {job: None for job in analytics_runs.JOBS}


def test_last_success_covers_every_job_even_with_partial_history(committed_conn):
    with analytics_runs.tracked_run(committed_conn, "BACKTEST"):
        pass

    last = analytics_runs.last_success(committed_conn)
    assert set(last) == set(analytics_runs.JOBS)
    assert last["BACKTEST"] is not None
    assert last["SECTORS"] is None
