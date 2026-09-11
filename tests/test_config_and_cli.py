"""Configuration invariants, the universe canary, and CLI smoke tests.

Coverage showed `companies.py` and `cli.py` at 0%. Neither is interesting
code, and both can fail in ways nothing else catches:

- A duplicated CIK in the tracked set would map two companies to one
  instrument, splicing their histories together. Same class of bug as keying
  on a ticker, arriving through the configuration instead.
- The CLI is the only interface to ingestion. A broken argument parser means
  the daily job does not run, and the symptom is staleness rather than an
  error.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from conftest import add_filing, utc
from investment_intelligence import cli
from investment_intelligence.companies import STAGE_ONE


# ---------------------------------------------------------------------------
# Tracked-set invariants
# ---------------------------------------------------------------------------

def test_no_cik_is_listed_twice():
    """Two entries sharing a CIK would resolve to one instrument and merge two
    companies' financial histories, with nothing looking wrong."""
    ciks = [c.cik for c in STAGE_ONE]
    assert len(ciks) == len(set(ciks)), "duplicate CIK in STAGE_ONE"


def test_no_ticker_is_listed_twice():
    tickers = [c.us_ticker for c in STAGE_ONE]
    assert len(tickers) == len(set(tickers))


def test_every_entry_is_complete():
    for company in STAGE_ONE:
        assert company.cik > 0
        assert company.us_ticker.strip()
        assert company.name.strip()


def test_the_expected_failure_is_still_documented_as_such():
    """ICICI Bank is in the tracked set deliberately: `companyfacts` 404s for
    it, so the gap report has something real to report. If someone removes the
    note without removing the company, the failure stops being expected and
    starts being noise."""
    icici = next((c for c in STAGE_ONE if c.us_ticker == "IBN"), None)
    if icici is not None:
        assert "FAIL" in icici.note.upper(), (
            "ICICI Bank is expected to fail; say so in the note or drop it")


def test_the_licence_note_quotes_a_source():
    """`sources` refuses a row without a licence note, but nothing stops the
    note being waffle. Phase 3's rule was: no source adopted on recollection."""
    note = cli.SOURCE_ROW["licence_note"]
    assert "free to access and reuse" in note
    assert cli.SOURCE_ROW["licence_url"].startswith("https://www.sec.gov")
    assert cli.SOURCE_ROW["redistributable"] is True
    assert isinstance(cli.SOURCE_ROW["verified_on"], date)


# ---------------------------------------------------------------------------
# The universe canary
# ---------------------------------------------------------------------------

def test_a_company_with_a_filing_is_in_the_universe(conn, instrument, source):
    """The canary for the Phase 9 bug.

    Every screen returned zero matches because `universe_as_of` required
    lifecycle events the EDGAR adapter never ingests. It failed closed, and
    "no companies match your criteria" is a completely ordinary thing for a
    screener to say — so nothing looked broken.

    This asserts the floor: given a filing, the company is screenable. Any
    future change to universe membership that reintroduces a hidden
    requirement fails here rather than silently emptying the product.
    """
    add_filing(conn, instrument, source, period_end=date(2025, 3, 31),
               filed_at=utc(2025, 6, 1), ref="canary")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM universe_as_of(%s, %s)",
            (date(2025, 12, 31), utc(2026, 1, 1)))
        assert cur.fetchone()[0] == 1, (
            "a company with a filing is not in the universe — every screen "
            "will return nothing and look like 'no matches'")


def test_universe_membership_needs_no_lifecycle_data(conn, instrument, source):
    """Explicitly: lifecycle events only ever subtract (migration 014).

    A source that supplies no lifecycle data must still produce a working
    product, because the only source we have supplies none.
    """
    add_filing(conn, instrument, source, period_end=date(2025, 3, 31),
               filed_at=utc(2025, 6, 1), ref="no-lifecycle")
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM instrument_lifecycle")
        assert cur.fetchone()[0] == 0, "fixture unexpectedly has lifecycle rows"
        cur.execute("SELECT count(*) FROM universe_as_of(%s, %s)",
                    (date(2025, 12, 31), utc(2026, 1, 1)))
        assert cur.fetchone()[0] == 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@pytest.fixture
def cli_env(committed_conn, migrated_db, monkeypatch):
    """Point the CLI at the test database.

    The CLI opens its own connection from DATABASE_URL, which is the whole
    point of testing it: the wiring between environment and database is where
    a deployment breaks.
    """
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    return committed_conn


def test_cli_rejects_an_unknown_command(cli_env):
    with pytest.raises(SystemExit) as exc:
        cli.main(["not-a-command"])
    assert exc.value.code != 0


def test_cli_requires_a_command(cli_env):
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code != 0


def test_seed_is_idempotent(cli_env, capsys):
    """Run twice, same state. The deploy workflow runs seed before ingestion,
    so a seed that duplicated rows on the second deploy would corrupt identity."""
    cli.main(["seed"])
    cli.main(["seed"])
    with cli_env.cursor() as cur:
        cur.execute("SELECT count(*) FROM instrument_external_ids WHERE scheme='SEC_CIK'")
        assert cur.fetchone()[0] == len(STAGE_ONE)
        cur.execute("SELECT count(*) FROM tracked_instruments")
        assert cur.fetchone()[0] == len(STAGE_ONE)


def test_seed_records_the_licence(cli_env):
    cli.main(["seed"])
    with cli_env.cursor() as cur:
        cur.execute("SELECT licence_note, redistributable, verified_on FROM sources "
                    "WHERE source_id = %s", (cli.SOURCE_ID,))
        note, redistributable, verified = cur.fetchone()
    assert "free to access and reuse" in note
    assert redistributable is True and verified is not None


def test_schedule_then_health_reports_never_ran(cli_env, capsys):
    """A declared job that has not run must be reported, and must exit
    non-zero so a scheduler surfaces it."""
    cli.main(["seed"])
    cli.main(["schedule", "--max-age-days", "3"])
    with pytest.raises(SystemExit) as exc:
        cli.main(["health"])
    assert exc.value.code == 1
    assert "NEVER_RAN" in capsys.readouterr().out


def test_health_exits_non_zero_when_nothing_is_scheduled(cli_env, capsys):
    """An unmonitored system cannot be healthy, only unobserved."""
    with pytest.raises(SystemExit) as exc:
        cli.main(["health"])
    assert exc.value.code == 1
    assert "nothing is being monitored" in capsys.readouterr().out


def test_gaps_distinguishes_unplanned_from_complete(cli_env, capsys):
    """`gaps()` returns an empty dict in two opposite situations: everything
    DONE, and nothing ever planned. Reporting "no gaps" for the second is the
    reassuring answer to the wrong question — the same fail-quiet shape as the
    Phase 9 empty-universe bug.

    Found while wiring CI, where fixture data was loaded under a different job
    name and `gaps` cheerfully reported that every instrument was DONE.
    """
    cli.main(["seed"])
    with pytest.raises(SystemExit) as exc:
        cli.main(["gaps"])
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "no checkpoints" in out
    assert "no gaps" not in out


def test_gaps_reports_completion_with_a_count(cli_env, capsys):
    """And when it IS complete, it says how many — so "all DONE" over an empty
    set is impossible to state."""
    from datetime import date as _date

    from investment_intelligence.ingest import backfill as _backfill
    from investment_intelligence.sources.fixture import FixtureSource

    cli.main(["seed"])
    with cli_env.cursor() as cur:
        cur.execute("SELECT value FROM instrument_external_ids WHERE scheme='SEC_CIK'")
        refs = [r[0] for r in cur.fetchall()]
    cli_env.commit()

    # Every ref known, each with one document, so all land DONE.
    from conftest import utc as _utc
    from investment_intelligence.sources.base import FilingDocument, ReportedFact

    def _doc(ref: str) -> FilingDocument:
        return FilingDocument(
            instrument_ref=ref, filing_type="ANNUAL_REPORT",
            period_end=_date(2025, 3, 31), filed_at=_utc(2025, 6, 1),
            source_ref=f"https://example.test/{ref}", content_hash=f"h-{ref}",
            facts=(ReportedFact(line_item="REVENUE", value=Decimal("1000"),
                                basis="CONSOLIDATED", fiscal_year=2025,
                                period_type="ANNUAL",
                                period_start=_date(2024, 4, 1),
                                period_end=_date(2025, 3, 31)),))

    src = FixtureSource(source_id=cli.SOURCE_ID, key_scheme="SEC_CIK",
                        documents={ref: [_doc(ref)] for ref in refs})
    _backfill.run(cli_env, src, job=cli.JOB,
                  since=_date(2000, 1, 1), until=_date(2040, 1, 1))

    cli.main(["gaps"])
    out = capsys.readouterr().out
    assert f"all {len(refs)} tracked instruments are DONE" in out
