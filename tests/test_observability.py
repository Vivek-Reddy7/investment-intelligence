"""Phase 16 exit criteria, as tests.

    A deliberately broken ingestion run produces an alert within one cycle.
    Staleness is visible to users, not only to us.

The failure this phase guards is the quietest in the project: data stops
updating and every page still renders, with confident stale numbers. Phase 8
built the detection; this tests that detection becomes a notification, once,
and clears when the condition does.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import date
from decimal import Decimal

import pytest

from conftest import add_fact, add_filing, utc
from investment_intelligence.ingest import incremental
from investment_intelligence.observability import logging as structured
from investment_intelligence.observability import status
from investment_intelligence.sources.base import FilingDocument, ReportedFact
from investment_intelligence.sources.fixture import FixtureSource

FY = 2025


@pytest.fixture
def wired(committed_conn, source_committed):
    """Two tracked companies and a declared schedule, so health is meaningful."""
    conn = committed_conn
    refs = ["2000001", "2000002"]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sources (source_id, name, kind, licence_note, "
            "redistributable, verified_on) VALUES ('FIXTURE','Fixture','FILINGS',"
            "'Test fixture.', true, current_date) ON CONFLICT DO NOTHING")
        for ref in refs:
            cur.execute("INSERT INTO instruments DEFAULT VALUES RETURNING instrument_id")
            iid = cur.fetchone()[0]
            cur.execute("INSERT INTO tracked_instruments (instrument_id) VALUES (%s)", (iid,))
            cur.execute(
                "INSERT INTO instrument_external_ids (instrument_id, scheme, value, "
                "source_id) VALUES (%s,'SEC_CIK',%s,'FIXTURE')", (iid, ref))
        cur.execute(
            "INSERT INTO ingestion_schedule (source_id, kind, max_age) "
            "VALUES ('FIXTURE','FUNDAMENTALS', interval '1 day')")
    conn.commit()

    def doc(ref):
        return FilingDocument(
            instrument_ref=ref, filing_type="ANNUAL_REPORT",
            period_end=date(FY, 3, 31), filed_at=utc(FY, 6, 1),
            source_ref=f"https://example.test/{ref}", content_hash=f"h-{ref}",
            facts=(ReportedFact(line_item="REVENUE", value=Decimal("1000"),
                                basis="CONSOLIDATED", fiscal_year=FY,
                                period_type="ANNUAL",
                                period_start=date(FY - 1, 4, 1),
                                period_end=date(FY, 3, 31)),))

    return conn, FixtureSource(source_id="FIXTURE",
                               documents={r: [doc(r)] for r in refs}), refs


def _open_alerts(conn) -> list[dict]:
    return [a for a in status.snapshot(conn)["open_alerts"]]


# ---------------------------------------------------------------------------
# The exit criterion
# ---------------------------------------------------------------------------

def test_a_broken_ingestion_run_produces_an_alert_within_one_cycle(wired):
    """Every company fails, so no run ever succeeds. One detection cycle must
    turn that into a CRITICAL alert pointing at the source, not the
    scheduler."""
    conn, src, refs = wired
    src.fail_on = set(refs)
    incremental.run(conn, src, refresh_metrics=False)

    status.detect(conn)
    alerts = _open_alerts(conn)

    assert len(alerts) == 1
    assert alerts[0]["severity"] == "CRITICAL"
    assert alerts[0]["status"] == "NEVER_SUCCEEDED"
    assert "the source, not the scheduler" in alerts[0]["detail"]


def test_a_scheduled_job_that_never_ran_alerts_too(wired):
    """The case a run-log query cannot see, and the one GitHub's 60-day
    auto-disable produces. The detail must point at the scheduler."""
    conn, _, _ = wired
    status.detect(conn)
    alerts = _open_alerts(conn)
    assert len(alerts) == 1
    assert alerts[0]["status"] == "NEVER_RAN"
    assert "scheduler" in alerts[0]["detail"]


def test_staleness_alerts_and_names_the_limit(wired):
    conn, src, _ = wired
    incremental.run(conn, src, refresh_metrics=False)
    with conn.cursor() as cur:
        cur.execute("UPDATE ingestion_runs SET started_at = now() - interval '10 days', "
                    "finished_at = now() - interval '10 days'")
    conn.commit()

    status.detect(conn)
    alerts = _open_alerts(conn)
    assert alerts[0]["status"] == "STALE"
    assert "serving stale numbers" in alerts[0]["detail"]


def test_a_healthy_system_produces_no_alert(wired):
    conn, src, _ = wired
    incremental.run(conn, src, refresh_metrics=False)
    status.detect(conn)
    assert _open_alerts(conn) == []


# ---------------------------------------------------------------------------
# Edge-triggered, like user alerts
# ---------------------------------------------------------------------------

def test_a_persistent_condition_alerts_once_not_every_cycle(wired):
    """An alert that fires every cycle while a condition holds trains whoever
    receives it to filter the channel — which is worse than no alerting,
    because the next real one is filtered too."""
    conn, _, _ = wired
    for _ in range(5):
        status.detect(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM app.operational_alerts")
        assert cur.fetchone()[0] == 1


def test_an_alert_resolves_when_the_condition_clears(wired):
    """Without this, the status page shows a permanent red light for something
    already fixed — which is how a dashboard stops being read."""
    conn, src, _ = wired
    status.detect(conn)
    assert _open_alerts(conn)

    incremental.run(conn, src, refresh_metrics=False)
    transitions = status.detect(conn)

    assert any(t["action"] == "RESOLVED" for t in transitions)
    assert _open_alerts(conn) == []


def test_a_resolved_alert_is_kept_not_deleted(wired):
    """The record of what broke and when is worth keeping."""
    conn, src, _ = wired
    status.detect(conn)
    incremental.run(conn, src, refresh_metrics=False)
    status.detect(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM app.operational_alerts "
                    "WHERE resolved_at IS NOT NULL")
        assert cur.fetchone()[0] == 1


def test_a_condition_that_recurs_opens_a_new_alert(wired):
    """Resolution re-arms detection, the same way user alerts do."""
    conn, src, refs = wired
    status.detect(conn)                                   # NEVER_RAN
    incremental.run(conn, src, refresh_metrics=False)     # healthy
    status.detect(conn)                                   # resolves
    with conn.cursor() as cur:
        cur.execute("UPDATE ingestion_runs SET started_at = now() - interval '10 days', "
                    "finished_at = now() - interval '10 days'")
    conn.commit()
    status.detect(conn)                                   # STALE
    assert len(_open_alerts(conn)) == 1
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM app.operational_alerts")
        assert cur.fetchone()[0] == 2


# ---------------------------------------------------------------------------
# Firing is separate from notifying
# ---------------------------------------------------------------------------

def test_detection_and_notification_are_separate_steps(wired):
    """A failing notification channel must neither lose an alert nor stall
    detection."""
    conn, _, _ = wired
    status.detect(conn)
    pending = status.pending_notifications(conn)
    assert len(pending) == 1

    status.mark_notified(conn, pending[0]["alert_id"])
    assert status.pending_notifications(conn) == []


def test_a_failed_notification_is_recorded_and_the_alert_stays_open(wired):
    conn, _, _ = wired
    status.detect(conn)
    pending = status.pending_notifications(conn)
    status.mark_notified(conn, pending[0]["alert_id"], error="smtp timeout")
    with conn.cursor() as cur:
        cur.execute("SELECT notify_error, resolved_at FROM app.operational_alerts")
        error, resolved = cur.fetchone()
    assert error == "smtp timeout"
    assert resolved is None, "a notification failure must not resolve the alert"


# ---------------------------------------------------------------------------
# The snapshot
# ---------------------------------------------------------------------------

def test_an_unmonitored_system_is_not_reported_as_healthy(committed_conn):
    """Nothing scheduled means nothing observed, which is not the same as
    working. Same rule as the `health` command's exit code."""
    assert status.snapshot(committed_conn)["overall"] == "UNMONITORED"


def test_an_inert_quality_check_degrades_the_overall_verdict(wired):
    """Phase 17's rule, enforced at the top level: zero findings over zero
    evaluable periods is an inert check, not clean data, so a status page must
    not read green because of it."""
    conn, _, _ = wired
    snap = status.snapshot(conn)
    assert snap["inert_checks"], "expected inert checks with no facts loaded"
    assert snap["overall"] != "OK"


def test_the_snapshot_reports_coverage_and_rejections(wired):
    conn, src, _ = wired
    incremental.run(conn, src, refresh_metrics=True)
    snap = status.snapshot(conn)
    assert snap["coverage"]["facts"] > 0
    assert snap["recent_runs"]
    assert snap["metrics"]


def test_a_run_stuck_in_running_is_visible(wired):
    """A process that died without finishing leaves RUNNING behind, which no
    outcome-based query would notice."""
    conn, _, _ = wired
    with conn.cursor() as cur:
        cur.execute("INSERT INTO ingestion_runs (source_id, kind) "
                    "VALUES ('FIXTURE','FUNDAMENTALS')")
    conn.commit()
    metrics = status.snapshot(conn)["metrics"]
    assert any(m["still_running"] > 0 for m in metrics)


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------

def test_log_lines_are_json_with_a_run_id():
    """Correlation is the point: a log without a run id is a pile of
    statements."""
    stream = io.StringIO()
    structured.configure("INFO", stream=stream)
    logger = logging.getLogger("test.correlation")

    with structured.run_context(47, source_id="FIXTURE"):
        structured.info(logger, "something happened", instruments=3)

    line = json.loads(stream.getvalue().strip())
    assert line["run_id"] == "47"
    assert line["source_id"] == "FIXTURE"
    assert line["instruments"] == 3
    assert line["level"] == "INFO"
    assert line["message"] == "something happened"


def test_nested_contexts_merge_rather_than_replace():
    stream = io.StringIO()
    structured.configure("INFO", stream=stream)
    logger = logging.getLogger("test.nesting")
    with structured.run_context(9, job="backfill"):
        with structured.run_context(9, instrument_ref="123"):
            structured.info(logger, "inner")
    line = json.loads(stream.getvalue().strip())
    assert line["job"] == "backfill" and line["instrument_ref"] == "123"


def test_the_run_id_does_not_leak_outside_its_context():
    stream = io.StringIO()
    structured.configure("INFO", stream=stream)
    logger = logging.getLogger("test.leak")
    with structured.run_context(1):
        pass
    structured.info(logger, "outside")
    assert "run_id" not in json.loads(stream.getvalue().strip())


@pytest.mark.parametrize("field", ["token", "password", "database_url", "email",
                                   "Authorization", "COOKIE"])
def test_sensitive_fields_are_redacted(field):
    """A token in a log is a credential somewhere nobody protects: CI logs are
    readable by anyone with repository access, and aggregators outlive the
    secrets they hold."""
    stream = io.StringIO()
    structured.configure("INFO", stream=stream)
    logger = logging.getLogger("test.redaction")
    structured.info(logger, "careless", **{field: "the-actual-secret"})
    line = json.loads(stream.getvalue().strip())
    assert line[field] == structured.REDACTED
    assert "the-actual-secret" not in stream.getvalue()


def test_an_exception_is_captured_in_the_line():
    stream = io.StringIO()
    structured.configure("INFO", stream=stream)
    logger = logging.getLogger("test.exc")
    try:
        raise ValueError("deliberate")
    except ValueError:
        logger.exception("caught it", extra={"fields": {}})
    line = json.loads(stream.getvalue().strip())
    assert "deliberate" in line["exception"]


def test_ingestion_emits_a_correlated_start_and_finish(wired):
    """The engine actually uses the context, rather than the logging module
    merely existing."""
    conn, src, _ = wired
    stream = io.StringIO()
    structured.configure("INFO", stream=stream)

    report = incremental.run(conn, src, refresh_metrics=False)

    lines = [json.loads(l) for l in stream.getvalue().strip().splitlines() if l]
    correlated = [l for l in lines if l.get("run_id") == str(report.run_id)]
    assert len(correlated) >= 2
    assert any(l["message"] == "incremental cycle started" for l in correlated)
    assert any(l["message"] == "incremental cycle finished" for l in correlated)
    finished = next(l for l in correlated if l["message"] == "incremental cycle finished")
    assert finished["outcome"] == "SUCCESS"
