"""Phase 7 exit criteria, as tests.

    Full universe loaded with target history depth.
    Re-running is a no-op.
    Gaps are enumerated, not hidden.

The second and third are the interesting ones. "Re-running is a no-op" is what
makes a multi-hour interruptible job survivable, and "gaps are enumerated" is
what stops a backfill that loaded 430 of 500 companies from reporting success.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from investment_intelligence.ingest import backfill
from investment_intelligence.ingest.writer import write_filing
from investment_intelligence.sources.base import (
    FilingDocument,
    InvalidFact,
    Rejection,
    ReportedFact,
)
from investment_intelligence.sources.fixture import FixtureSource

SINCE, UNTIL = date(2020, 1, 1), date(2030, 1, 1)
KNOWN = datetime(2026, 10, 14, tzinfo=timezone.utc)
LATER = datetime(2027, 1, 20, tzinfo=timezone.utc)


def revenue(value: str, *, fy: int = 2026, period: str = "Q2") -> ReportedFact:
    return ReportedFact(
        line_item="REVENUE", value=Decimal(value), basis="CONSOLIDATED",
        fiscal_year=fy, period_type=period,
        period_start=date(2026, 7, 1), period_end=date(2026, 9, 30),
    )


def document(isin: str, *facts: ReportedFact, ref: str = "doc-1",
             hash_: str = "h1") -> FilingDocument:
    return FilingDocument(
        instrument_ref=isin, filing_type="QUARTERLY_RESULT", period_end=date(2026, 9, 30),
        filed_at=KNOWN, source_ref=ref, content_hash=hash_, facts=facts,
    )


@pytest.fixture
def universe(committed_conn, source_committed):
    """Five tracked instruments, so partial failure is observable."""
    conn = committed_conn
    # 12 characters, matching the ISIN check constraint in 002_instruments.sql:
    # two letters, nine alphanumerics, one check digit.
    isins = [f"INE00000{n}001" for n in range(1, 6)]
    with conn.cursor() as cur:
        for isin in isins:
            cur.execute(
                "INSERT INTO instruments (isin) VALUES (%s) RETURNING instrument_id",
                (isin,),
            )
            iid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO tracked_instruments (instrument_id) VALUES (%s)", (iid,)
            )
            # Sources address instruments by their own identifier scheme
            # (base.FilingSource.key_scheme), never by ours.
            cur.execute(
                "INSERT INTO instrument_external_ids (instrument_id, scheme, value, "
                "source_id) VALUES (%s, 'SEC_CIK', %s, %s)",
                (iid, isin, source_committed),
            )
    return isins


@pytest.fixture
def fixture_source(committed_conn, universe, source_committed):
    """A FixtureSource registered as a real source row, so the FK is satisfied."""
    conn = committed_conn
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sources (source_id, name, kind, licence_note, "
            "redistributable, verified_on) VALUES ('FIXTURE', 'Fixture', "
            "'FILINGS', 'Test fixture, not a licence position.', true, %s) "
            "ON CONFLICT (source_id) DO NOTHING",
            (date(2026, 9, 11),),
        )
    return FixtureSource(documents={
        isin: [document(isin, revenue("1000"))] for isin in universe
    })


# ---------------------------------------------------------------------------
# Idempotency: the dedup-on-value rule
# ---------------------------------------------------------------------------

def _versions(conn, isin: str) -> list[Decimal]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT f.value FROM financial_facts f JOIN instruments i USING "
            "(instrument_id) WHERE i.isin = %s AND f.line_item = 'REVENUE' "
            "ORDER BY f.known_from",
            (isin,),
        )
        return [r[0] for r in cur.fetchall()]


def test_writing_the_same_value_twice_creates_one_version(committed_conn, fixture_source, universe):
    """Re-ingesting an unchanged figure must not append a version. Otherwise a
    year of daily runs multiplies the store by 365 and buries real changes."""
    conn = committed_conn
    isin = universe[0]
    doc = document(isin, revenue("1000"))

    first = write_filing(conn, doc, source_id="FIXTURE", key_scheme="SEC_CIK", known_from=KNOWN)
    second = write_filing(conn, doc, source_id="FIXTURE", key_scheme="SEC_CIK", known_from=LATER)

    assert first.facts_written == 1
    assert second.facts_written == 0
    assert second.facts_unchanged == 1
    assert _versions(conn, isin) == [Decimal("1000")]


def test_writing_a_changed_value_creates_a_second_version(committed_conn, fixture_source, universe):
    """The other half, and the one whose failure is invisible: a dropped
    restatement leaves a confidently wrong number on screen with no error."""
    conn = committed_conn
    isin = universe[0]
    write_filing(conn, document(isin, revenue("1000")),
                 source_id="FIXTURE", key_scheme="SEC_CIK", known_from=KNOWN)
    write_filing(conn, document(isin, revenue("900"), ref="doc-2", hash_="h2"),
                 source_id="FIXTURE", key_scheme="SEC_CIK", known_from=LATER)

    assert _versions(conn, isin) == [Decimal("1000"), Decimal("900")]


def test_a_value_reverting_creates_a_third_version(committed_conn, fixture_source, universe):
    """1000 -> 900 -> 1000. A UNIQUE over the value would reject this and lose
    the fact that the figure moved back."""
    conn = committed_conn
    isin = universe[0]
    for value, ref, when in (("1000", "d1", KNOWN), ("900", "d2", LATER),
                             ("1000", "d3", datetime(2027, 6, 1, tzinfo=timezone.utc))):
        write_filing(conn, document(isin, revenue(value), ref=ref, hash_=ref),
                     source_id="FIXTURE", key_scheme="SEC_CIK", known_from=when)
    assert _versions(conn, isin) == [Decimal("1000"), Decimal("900"), Decimal("1000")]


def test_the_same_document_refetched_is_one_filing(committed_conn, fixture_source, universe):
    conn = committed_conn
    doc = document(universe[0], revenue("1000"))
    assert write_filing(conn, doc, source_id="FIXTURE", key_scheme="SEC_CIK", known_from=KNOWN).filings_written == 1
    again = write_filing(conn, doc, source_id="FIXTURE", key_scheme="SEC_CIK", known_from=LATER)
    assert again.filings_written == 0
    assert again.filings_seen_before == 1


# ---------------------------------------------------------------------------
# Resumption
# ---------------------------------------------------------------------------

def test_backfill_loads_the_whole_universe(committed_conn, fixture_source, universe):
    conn = committed_conn
    report = backfill.run(conn, fixture_source, job="j", since=SINCE, until=UNTIL)
    assert report.instruments_done == len(universe)
    assert report.outcome == "SUCCESS"
    assert backfill.gaps(conn, "j") == {}


def test_rerunning_a_completed_backfill_does_nothing(committed_conn, fixture_source, universe):
    """The exit criterion. Note what is asserted: not merely that no rows were
    written, but that the source was never CALLED again. A backfill that
    re-fetches completed work is not resumable, it is just idempotent -- and it
    still burns hours of someone else's rate limit."""
    conn = committed_conn
    backfill.run(conn, fixture_source, job="j", since=SINCE, until=UNTIL)
    calls_after_first = len(fixture_source.calls)

    second = backfill.run(conn, fixture_source, job="j", since=SINCE, until=UNTIL)

    assert len(fixture_source.calls) == calls_after_first, "resumed run re-fetched"
    assert second.instruments_done == 0
    assert second.writes.facts_written == 0


def test_a_failing_company_does_not_abandon_the_others(committed_conn, fixture_source, universe):
    conn = committed_conn
    fixture_source.fail_on = {universe[2]}
    report = backfill.run(conn, fixture_source, job="j", since=SINCE, until=UNTIL)

    assert report.instruments_done == 4
    assert report.instruments_failed == 1
    assert report.outcome == "PARTIAL"


def test_a_resumed_run_retries_only_what_failed(committed_conn, fixture_source, universe):
    conn = committed_conn
    failing = universe[2]
    fixture_source.fail_on = {failing}
    backfill.run(conn, fixture_source, job="j", since=SINCE, until=UNTIL)

    fixture_source.fail_on = set()
    fixture_source.calls.clear()
    second = backfill.run(conn, fixture_source, job="j", since=SINCE, until=UNTIL)

    assert fixture_source.calls == [failing]
    assert second.instruments_done == 1
    assert backfill.gaps(conn, "j") == {}


def test_an_interrupted_run_resumes_where_it_stopped(committed_conn, fixture_source, universe):
    """Simulates the real failure: the process dies part-way through.

    `max_instruments` is how the engine survives a scheduler job limit -- run a
    slice, commit, exit cleanly. Three slices of two should cover five
    companies with no repeats.
    """
    conn = committed_conn
    seen: list[str] = []
    for _ in range(3):
        fixture_source.calls.clear()
        backfill.run(conn, fixture_source, job="j", since=SINCE, until=UNTIL,
                     max_instruments=2)
        seen.extend(fixture_source.calls)

    assert sorted(seen) == sorted(universe), "a company was skipped or fetched twice"
    assert backfill.gaps(conn, "j") == {}


def test_a_crash_mid_instrument_is_safe_to_retry(committed_conn, fixture_source, universe):
    """The property that makes checkpointing coarse-grained acceptable.

    A crash after writing some of a company's facts leaves the checkpoint
    IN_PROGRESS. The retry re-fetches the whole company, and because writes are
    idempotent on (identity, value) the already-written facts produce no new
    versions. So we never need to know how far through the crash happened.
    """
    conn = committed_conn
    isin = universe[0]
    write_filing(conn, document(isin, revenue("1000")),
                 source_id="FIXTURE", key_scheme="SEC_CIK", known_from=KNOWN)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO backfill_checkpoints (job, instrument_id, state) "
            "SELECT 'j', instrument_id, 'IN_PROGRESS' FROM instruments WHERE isin = %s",
            (isin,),
        )

    backfill.run(conn, fixture_source, job="j", since=SINCE, until=UNTIL)

    assert _versions(conn, isin) == [Decimal("1000")], "retry duplicated a fact"


# ---------------------------------------------------------------------------
# Gaps and rejections
# ---------------------------------------------------------------------------

def test_a_company_with_no_filings_is_skipped_not_completed(committed_conn, fixture_source, universe):
    """SKIPPED and DONE must stay distinct. Collapsing them makes "loaded
    nothing" indistinguishable from "loaded successfully", which is exactly
    how a backfill silently covers 86% of the universe."""
    conn = committed_conn
    empty = universe[4]
    fixture_source.documents[empty] = []

    report = backfill.run(conn, fixture_source, job="j", since=SINCE, until=UNTIL)

    assert report.instruments_skipped == 1
    assert backfill.gaps(conn, "j") == {"SKIPPED": [empty]}


def test_gaps_name_the_failure(committed_conn, fixture_source, universe):
    conn = committed_conn
    fixture_source.fail_on = {universe[1]}
    backfill.run(conn, fixture_source, job="j", since=SINCE, until=UNTIL)

    reported = backfill.gaps(conn, "j")
    assert "FAILED" in reported
    assert universe[1] in reported["FAILED"][0]
    assert "ConnectionError" in reported["FAILED"][0]


def test_rejected_documents_are_counted_not_imputed(committed_conn, fixture_source, universe):
    conn = committed_conn
    isin = universe[0]
    fixture_source.documents[isin] = [
        Rejection(instrument_ref=isin, detail="Q2 revenue", reason="value was null in filing"),
        document(isin, revenue("1000")),
    ]
    report = backfill.run(conn, fixture_source, job="j", since=SINCE, until=UNTIL)

    assert len(report.rejections) == 1
    assert report.rejections[0].reason == "value was null in filing"
    # The good document still landed. One bad row does not discard the filing.
    assert _versions(conn, isin) == [Decimal("1000")]


def test_run_log_records_the_backfill(committed_conn, fixture_source, universe):
    conn = committed_conn
    report = backfill.run(conn, fixture_source, job="j", since=SINCE, until=UNTIL)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT outcome, rows_written, finished_at FROM ingestion_runs "
            "WHERE run_id = %s",
            (report.run_id,),
        )
        outcome, rows, finished = cur.fetchone()
    assert outcome == "SUCCESS"
    assert rows == len(universe)
    assert finished is not None


def test_a_failed_backfill_still_writes_a_run_row(committed_conn, fixture_source, universe):
    """A failed run is a row, not an absence."""
    conn = committed_conn
    fixture_source.fail_on = set(universe)
    report = backfill.run(conn, fixture_source, job="j", since=SINCE, until=UNTIL)

    with conn.cursor() as cur:
        cur.execute("SELECT outcome, error FROM ingestion_runs WHERE run_id = %s",
                    (report.run_id,))
        outcome, error = cur.fetchone()
    assert outcome == "FAILED"
    assert error is not None


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def test_rate_limiter_waits_between_calls():
    slept: list[float] = []
    clock = iter([0.0, 0.0, 0.5, 0.5, 2.0, 2.0])
    limiter = backfill.RateLimiter(1.0, sleep=slept.append, clock=lambda: next(clock))

    limiter.wait()   # first call: no wait
    limiter.wait()   # 0.5s elapsed of a 1.0s interval -> sleep 0.5
    limiter.wait()   # 1.5s elapsed -> no wait

    assert slept == [0.5]


def test_rate_limiter_does_not_wait_before_the_first_call():
    slept: list[float] = []
    backfill.RateLimiter(5.0, sleep=slept.append, clock=lambda: 0.0).wait()
    assert slept == []


# ---------------------------------------------------------------------------
# Boundary validation
# ---------------------------------------------------------------------------

def test_a_float_value_is_refused():
    """Floats lose precision on money and the loss is invisible in the output."""
    with pytest.raises(InvalidFact, match="Decimal"):
        ReportedFact(
            line_item="REVENUE", value=1000.0, basis="CONSOLIDATED",
            fiscal_year=2026, period_type="Q2",
            period_start=date(2026, 7, 1), period_end=date(2026, 9, 30),
        )


def test_a_period_label_that_disagrees_with_its_dates_is_refused():
    """A quarter spanning a year means the adapter mapped something wrongly,
    and every metric computed from it inherits the error silently."""
    with pytest.raises(InvalidFact, match="should span"):
        ReportedFact(
            line_item="REVENUE", value=Decimal("1000"), basis="CONSOLIDATED",
            fiscal_year=2026, period_type="Q2",
            period_start=date(2026, 4, 1), period_end=date(2027, 3, 31),
        )


def test_an_inverted_period_is_refused():
    with pytest.raises(InvalidFact, match="ends before it starts"):
        ReportedFact(
            line_item="REVENUE", value=Decimal("1000"), basis="CONSOLIDATED",
            fiscal_year=2026, period_type="Q2",
            period_start=date(2026, 9, 30), period_end=date(2026, 7, 1),
        )


def test_an_unknown_basis_is_refused():
    with pytest.raises(InvalidFact, match="basis"):
        ReportedFact(
            line_item="REVENUE", value=Decimal("1000"), basis="COMBINED",
            fiscal_year=2026, period_type="Q2",
            period_start=date(2026, 7, 1), period_end=date(2026, 9, 30),
        )


def test_a_filing_without_provenance_is_refused():
    with pytest.raises(InvalidFact, match="source_ref"):
        FilingDocument(
            instrument_ref="INE000TEST01", filing_type="QUARTERLY_RESULT",
            period_end=date(2026, 9, 30), filed_at=KNOWN,
            source_ref="", content_hash="h",
        )


def test_a_naive_filing_timestamp_is_refused():
    """A timestamp without a timezone is a transaction-time bug waiting to
    happen, and known_from ordering is what the whole product rests on."""
    with pytest.raises(InvalidFact, match="timezone-aware"):
        FilingDocument(
            instrument_ref="INE000TEST01", filing_type="QUARTERLY_RESULT",
            period_end=date(2026, 9, 30), filed_at=datetime(2026, 10, 14),
            source_ref="r", content_hash="h",
        )
