"""Phase 8 exit criteria, as tests.

    Runs unattended for a week.
    A failed run is visible as a row.
    A simulated restatement produces a new version, not an overwrite.

"Runs unattended for a week" cannot be tested in a test suite, so what is
tested instead is the property that makes it true: repeated cycles over
unchanged data write nothing and cost nothing. A job that is cheap when there
is nothing to do is a job that can run every day forever.

The failure this phase guards against is the quietest in the project. A
backfill that half-finishes leaves a checkpoint table saying so. An
incremental job that stops running leaves nothing at all -- the site serves
yesterday's numbers, confidently, and every page still renders.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from investment_intelligence.ingest import incremental
from investment_intelligence.sources.base import FilingDocument, ReportedFact
from investment_intelligence.sources.fixture import FixtureSource

TODAY = date.today()
FY = TODAY.year - 1


def fact(value: str, line_item: str = "REVENUE") -> ReportedFact:
    return ReportedFact(
        line_item=line_item, value=Decimal(value), basis="CONSOLIDATED",
        fiscal_year=FY, period_type="ANNUAL",
        period_start=date(FY - 1, 4, 1), period_end=date(FY, 3, 31),
    )


def doc(ref: str, *facts: ReportedFact, filed: datetime, accn: str) -> FilingDocument:
    return FilingDocument(
        instrument_ref=ref, filing_type="ANNUAL_REPORT",
        period_end=date(FY, 3, 31), filed_at=filed,
        source_ref=f"https://example.test/{accn}", content_hash=accn, facts=facts,
    )


FILED_FIRST = datetime(FY, 6, 1, tzinfo=timezone.utc)
FILED_LATER = datetime(FY + 1, 1, 15, tzinfo=timezone.utc)


@pytest.fixture
def wired(committed_conn, source_committed):
    """Three tracked companies, addressed by SEC_CIK, with a fixture source."""
    conn = committed_conn
    refs = ["1000001", "1000002", "1000003"]
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
            "VALUES ('FIXTURE','FUNDAMENTALS', interval '3 days') ON CONFLICT DO NOTHING")
    conn.commit()

    src = FixtureSource(source_id="FIXTURE", documents={
        ref: [doc(ref, fact("1000"), filed=FILED_FIRST, accn=f"acc-{ref}-1")]
        for ref in refs
    })
    return conn, src, refs


def _versions(conn, ref: str, line_item: str = "REVENUE") -> list[Decimal]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT f.value FROM financial_facts f "
            "JOIN instrument_external_ids x USING (instrument_id) "
            "WHERE x.scheme='SEC_CIK' AND x.value=%s AND f.line_item=%s "
            "ORDER BY f.known_from", (ref, line_item))
        return [r[0] for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# The first cycle, and every cycle after it
# ---------------------------------------------------------------------------

def test_first_cycle_loads_everything(wired):
    conn, src, refs = wired
    report = incremental.run(conn, src, refresh_metrics=False)
    assert report.outcome == "SUCCESS"
    assert report.instruments_checked == len(refs)
    assert report.new_filings == len(refs)
    assert report.writes.facts_written == len(refs)


def test_a_second_cycle_writes_nothing_and_fetches_nothing_new(wired):
    """The property that makes "runs unattended for a week" true.

    Note it asserts new_filings == 0, not merely facts_written == 0. The job
    recognises a document it has already parsed and skips it, so the daily
    cost is proportional to what is NEW rather than to the whole history.
    Relying on the writer's dedup alone would be correct and would get slower
    every year.
    """
    conn, src, _ = wired
    incremental.run(conn, src, refresh_metrics=False)
    second = incremental.run(conn, src, refresh_metrics=False)

    assert second.new_filings == 0
    assert second.writes.facts_written == 0
    assert second.outcome == "SUCCESS"


def test_seven_consecutive_cycles_stay_a_no_op(wired):
    """A week, compressed. Nothing accumulates: not versions, not filings."""
    conn, src, refs = wired
    incremental.run(conn, src, refresh_metrics=False)
    for _ in range(7):
        report = incremental.run(conn, src, refresh_metrics=False)
        assert report.writes.facts_written == 0

    assert _versions(conn, refs[0]) == [Decimal("1000")]
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM filings")
        assert cur.fetchone()[0] == len(refs)


# ---------------------------------------------------------------------------
# Restatement — the thing the product exists for
# ---------------------------------------------------------------------------

def test_a_restatement_arriving_later_creates_a_new_version(wired):
    conn, src, refs = wired
    incremental.run(conn, src, refresh_metrics=False)

    # A revised document: new accession, later filing date, different figure.
    target = refs[0]
    src.documents[target].append(
        doc(target, fact("900"), filed=FILED_LATER, accn=f"acc-{target}-2"))

    report = incremental.run(conn, src, refresh_metrics=False)

    assert report.new_filings == 1
    assert _versions(conn, target) == [Decimal("1000"), Decimal("900")]


def test_the_restated_version_is_dated_by_its_filing_not_by_the_run(wired):
    """known_from must be the filing date, or the transaction-time axis
    collapses onto "whenever the job happened to run" and every historical
    query silently returns today's view."""
    conn, src, refs = wired
    incremental.run(conn, src, refresh_metrics=False)
    target = refs[0]
    src.documents[target].append(
        doc(target, fact("900"), filed=FILED_LATER, accn=f"acc-{target}-2"))
    incremental.run(conn, src, refresh_metrics=False)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT f.value, f.known_from FROM financial_facts f "
            "JOIN instrument_external_ids x USING (instrument_id) "
            "WHERE x.scheme='SEC_CIK' AND x.value=%s ORDER BY f.known_from", (target,))
        rows = cur.fetchall()
    assert [r[1].date() for r in rows] == [FILED_FIRST.date(), FILED_LATER.date()]


def test_a_reissued_document_with_the_same_accession_is_not_a_restatement(wired):
    """Same document seen twice is one filing. Only a genuinely new accession
    counts, which is why content_hash is the accession number."""
    conn, src, refs = wired
    incremental.run(conn, src, refresh_metrics=False)
    target = refs[0]
    src.documents[target].append(
        doc(target, fact("1000"), filed=FILED_LATER, accn=f"acc-{target}-1"))

    report = incremental.run(conn, src, refresh_metrics=False)
    assert report.new_filings == 0
    assert _versions(conn, target) == [Decimal("1000")]


# ---------------------------------------------------------------------------
# Failure is visible
# ---------------------------------------------------------------------------

def test_one_failing_company_does_not_end_the_cycle(wired):
    conn, src, refs = wired
    src.fail_on = {refs[1]}
    report = incremental.run(conn, src, refresh_metrics=False)
    assert report.instruments_checked == 3
    assert report.instruments_failed == 1
    assert report.outcome == "PARTIAL"
    assert _versions(conn, refs[0]) == [Decimal("1000")]


def test_a_totally_failed_cycle_is_recorded_as_failed(wired):
    conn, src, refs = wired
    src.fail_on = set(refs)
    report = incremental.run(conn, src, refresh_metrics=False)
    assert report.outcome == "FAILED"
    with conn.cursor() as cur:
        cur.execute("SELECT outcome, error, finished_at FROM ingestion_runs "
                    "WHERE run_id = %s", (report.run_id,))
        outcome, error, finished = cur.fetchone()
    assert outcome == "FAILED" and error and finished


def test_every_cycle_writes_a_run_row_even_when_nothing_changed(wired):
    """A failed run is a row, not an absence -- and so is an idle one.

    Without this, a scheduler that stopped firing and a week of quiet Sundays
    look identical in the run log.
    """
    conn, src, _ = wired
    for _ in range(3):
        incremental.run(conn, src, refresh_metrics=False)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ingestion_runs WHERE source_id = 'FIXTURE'")
        assert cur.fetchone()[0] == 3


# ---------------------------------------------------------------------------
# Health: detecting the run that did not happen
# ---------------------------------------------------------------------------

def test_health_reports_ok_after_a_successful_cycle(wired):
    conn, src, _ = wired
    incremental.run(conn, src, refresh_metrics=False)
    rows = {r["source_id"]: r for r in incremental.health(conn)}
    assert rows["FIXTURE"]["status"] == "OK"


def test_health_reports_a_job_that_has_never_run(committed_conn, source_committed):
    """The case a run-log query cannot see: there is no row to find."""
    conn = committed_conn
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ingestion_schedule (source_id, kind, max_age) "
            "VALUES (%s,'FUNDAMENTALS', interval '1 day')", (source_committed,))
    conn.commit()
    rows = {r["source_id"]: r for r in incremental.health(conn)}
    assert rows[source_committed]["status"] == "NEVER_RAN"
    assert incremental.unhealthy(conn)


def test_health_reports_stale_when_the_last_success_is_too_old(wired):
    """The GitHub Actions 60-day auto-disable, in miniature."""
    conn, src, _ = wired
    incremental.run(conn, src, refresh_metrics=False)
    with conn.cursor() as cur:
        # Backdate the successful run instead of shrinking max_age to a second,
        # which raced the clock: the run had just finished, so `age` was often
        # still under the threshold and the test flapped to OK.
        cur.execute("UPDATE ingestion_runs SET finished_at = now() - interval '10 days' "
                    "WHERE source_id = 'FIXTURE'")
    conn.commit()
    rows = {r["source_id"]: r for r in incremental.health(conn)}
    assert rows["FIXTURE"]["status"] == "STALE"


def test_a_job_that_ran_but_never_succeeded_is_not_reported_as_never_run(wired):
    """Different diagnoses, different actions.

    NEVER_RAN points at the scheduler -- a disabled workflow, a wrong cron, a
    missing secret. NEVER_SUCCEEDED points at the source -- the job is firing
    and the provider is refusing us. Migration 015 conflated them by testing
    last_success before last_outcome, which would have sent anyone
    investigating to the wrong place.
    """
    conn, src, refs = wired
    src.fail_on = set(refs)
    incremental.run(conn, src, refresh_metrics=False)
    rows = {r["source_id"]: r for r in incremental.health(conn)}
    assert rows["FIXTURE"]["status"] == "NEVER_SUCCEEDED"


def test_health_surfaces_a_partial_cycle_after_an_earlier_success(wired):
    conn, src, refs = wired
    incremental.run(conn, src, refresh_metrics=False)   # a clean cycle first
    src.fail_on = {refs[0]}
    src.documents[refs[1]].append(
        doc(refs[1], fact("900"), filed=FILED_LATER, accn="acc-partial"))
    incremental.run(conn, src, refresh_metrics=False)
    rows = {r["source_id"]: r for r in incremental.health(conn)}
    assert rows["FIXTURE"]["status"] == "LAST_RUN_PARTIAL"


def test_a_disabled_schedule_is_not_reported(wired):
    """Turning a job off should silence it, not leave a permanent red light
    that trains everyone to ignore the dashboard."""
    conn, src, _ = wired
    with conn.cursor() as cur:
        cur.execute("UPDATE ingestion_schedule SET enabled = false "
                    "WHERE source_id = 'FIXTURE'")
    conn.commit()
    assert all(r["source_id"] != "FIXTURE" for r in incremental.health(conn))


# ---------------------------------------------------------------------------
# Derived state must not lag the facts
# ---------------------------------------------------------------------------

def test_metrics_are_refreshed_after_ingestion(wired):
    """Fresh facts behind stale ratios is worse than being uniformly stale,
    because the two disagree and nothing on the page says which to trust."""
    conn, src, refs = wired
    with conn.cursor() as cur:
        for ref in refs:
            cur.execute(
                "SELECT instrument_id FROM instrument_external_ids "
                "WHERE scheme='SEC_CIK' AND value=%s", (ref,))
    src.documents[refs[0]] = [doc(refs[0], fact("1000"), fact("100", "NET_PROFIT"),
                                  filed=FILED_FIRST, accn="acc-metrics")]
    report = incremental.run(conn, src, refresh_metrics=True)
    assert report.metrics_refreshed > 0
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM metric_values WHERE metric_code='NET_MARGIN'")
        assert cur.fetchone()[0] > 0
