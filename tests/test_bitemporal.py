"""The Phase 6 exit criterion, as tests.

The exit criterion was deliberately specific: a point-in-time query returns
correct results against hand-seeded data *including a restatement and a
delisting*. Those two cases are the whole product. Everything else here
protects them.

These are the tests for failures that are invisible in the output. A
restatement silently overwriting the original figure produces no error and
nothing wrong-looking on screen -- it just means every historical screen is
quietly flattering. That class of bug is the reason this project stores data
the way it does, so it is the class of bug that gets the most tests.
"""

from __future__ import annotations

from datetime import date

import psycopg
import pytest

from conftest import add_fact, add_filing, add_lifecycle, utc


# ---------------------------------------------------------------------------
# Restatement: the core case
# ---------------------------------------------------------------------------

@pytest.fixture
def restated_revenue(conn, instrument, source):
    """Q2 FY2026 revenue reported as 1000, restated to 900 three months later.

    Original filed 2026-10-14. Restatement filed 2027-01-20.
    """
    original = add_filing(
        conn, instrument, source,
        period_end=date(2026, 9, 30), filed_at=utc(2026, 10, 14),
        ref="q2fy26-original",
    )
    add_fact(
        conn, instrument, source, original,
        line_item="REVENUE", value=1000, fiscal_year=2026, period_type="Q2",
        period_start=date(2026, 7, 1), period_end=date(2026, 9, 30),
        known_from=utc(2026, 10, 14),
    )

    restatement = add_filing(
        conn, instrument, source,
        period_end=date(2026, 9, 30), filed_at=utc(2027, 1, 20),
        ref="q2fy26-restated", filing_type="RESTATEMENT",
    )
    add_fact(
        conn, instrument, source, restatement,
        line_item="REVENUE", value=900, fiscal_year=2026, period_type="Q2",
        period_start=date(2026, 7, 1), period_end=date(2026, 9, 30),
        known_from=utc(2027, 1, 20),
    )
    return instrument


def _revenue_as_of(conn, instrument_id, as_of):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT value, known_from, filing_id
            FROM   facts_as_of(%s)
            WHERE  instrument_id = %s AND line_item = 'REVENUE'
            """,
            (as_of, instrument_id),
        )
        return cur.fetchall()


def test_restatement_creates_a_version_rather_than_an_overwrite(conn, restated_revenue):
    """Both versions exist. This is the mechanism; if it fails nothing else works."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT value FROM financial_facts "
            "WHERE instrument_id = %s AND line_item = 'REVENUE' "
            "ORDER BY known_from",
            (restated_revenue,),
        )
        assert [float(r[0]) for r in cur.fetchall()] == [1000.0, 900.0]


def test_as_of_before_restatement_returns_the_original_figure(conn, restated_revenue):
    """The point of the entire project.

    Someone screening in November 2026 could not have known about a January
    2027 restatement, so they must see 1000.
    """
    rows = _revenue_as_of(conn, restated_revenue, utc(2026, 11, 1))
    assert len(rows) == 1
    assert float(rows[0][0]) == 1000.0


def test_as_of_after_restatement_returns_the_restated_figure(conn, restated_revenue):
    rows = _revenue_as_of(conn, restated_revenue, utc(2027, 3, 1))
    assert len(rows) == 1
    assert float(rows[0][0]) == 900.0


def test_as_of_returns_exactly_one_row_per_fact_identity(conn, restated_revenue):
    """Two versions must not both surface. A duplicated fact would double-count
    in any aggregate built on top of it."""
    for as_of in (utc(2026, 11, 1), utc(2027, 3, 1), utc(2030, 1, 1)):
        assert len(_revenue_as_of(conn, restated_revenue, as_of)) == 1


def test_as_of_before_the_first_filing_returns_nothing(conn, restated_revenue):
    """No lookahead. In September 2026 the quarter had not been reported."""
    assert _revenue_as_of(conn, restated_revenue, utc(2026, 9, 1)) == []


def test_each_version_names_the_filing_it_came_from(conn, restated_revenue):
    """Provenance: the Phase 1 promise that every number is traceable."""
    before = _revenue_as_of(conn, restated_revenue, utc(2026, 11, 1))[0]
    after = _revenue_as_of(conn, restated_revenue, utc(2027, 3, 1))[0]
    assert before[2] != after[2], "restated value must cite a different filing"


def test_a_restatement_that_reverts_to_an_earlier_value_is_allowed(
    conn, instrument, source, restated_revenue
):
    """1000 -> 900 -> 1000 is a real sequence: a correction later withdrawn.

    This is why the uniqueness constraint is on (identity, known_from) and not
    on (identity, value). A constraint including the value would reject this
    and silently lose the third version.
    """
    third = add_filing(
        conn, instrument, source,
        period_end=date(2026, 9, 30), filed_at=utc(2027, 6, 10),
        ref="q2fy26-rerestated", filing_type="RESTATEMENT",
    )
    add_fact(
        conn, instrument, source, third,
        line_item="REVENUE", value=1000, fiscal_year=2026, period_type="Q2",
        period_start=date(2026, 7, 1), period_end=date(2026, 9, 30),
        known_from=utc(2027, 6, 10),
    )
    assert float(_revenue_as_of(conn, instrument, utc(2027, 8, 1))[0][0]) == 1000.0


def test_two_versions_cannot_share_an_instant(conn, instrument, source, restated_revenue):
    """Ambiguous ordering is rejected, because DISTINCT ON would pick arbitrarily."""
    dup = add_filing(
        conn, instrument, source,
        period_end=date(2026, 9, 30), filed_at=utc(2027, 1, 20),
        ref="q2fy26-duplicate", filing_type="RESTATEMENT",
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        add_fact(
            conn, instrument, source, dup,
            line_item="REVENUE", value=950, fiscal_year=2026, period_type="Q2",
            period_start=date(2026, 7, 1), period_end=date(2026, 9, 30),
            known_from=utc(2027, 1, 20),  # same instant as the restatement
        )


def test_standalone_and_consolidated_are_separate_facts(conn, instrument, source):
    """Indian companies report both, and they are different numbers. Collapsing
    them is a silent correctness bug, so basis is part of fact identity."""
    f = add_filing(
        conn, instrument, source,
        period_end=date(2026, 9, 30), filed_at=utc(2026, 10, 14), ref="q2-both",
    )
    for basis, value in (("CONSOLIDATED", 1000), ("STANDALONE", 800)):
        add_fact(
            conn, instrument, source, f,
            line_item="REVENUE", value=value, fiscal_year=2026, period_type="Q2",
            basis=basis, known_from=utc(2026, 10, 14),
        )
    assert len(_revenue_as_of(conn, instrument, utc(2026, 11, 1))) == 2


# ---------------------------------------------------------------------------
# Delisting: survivorship bias
# ---------------------------------------------------------------------------

@pytest.fixture
def delisted_company(conn, instrument, source):
    """Listed 2015-04-01, filed for Q2 FY2026, delisted 2027-03-31."""
    add_lifecycle(conn, instrument, source, event="LISTED",
                  event_date=date(2015, 4, 1), known_from=utc(2015, 4, 1))
    add_filing(conn, instrument, source, period_end=date(2026, 9, 30),
               filed_at=utc(2026, 10, 14), ref="pre-delist")
    add_lifecycle(conn, instrument, source, event="DELISTED",
                  event_date=date(2027, 3, 31), known_from=utc(2027, 3, 31))
    return instrument


def _universe(conn, on, as_of):
    with conn.cursor() as cur:
        cur.execute("SELECT instrument_id FROM universe_as_of(%s, %s)", (on, as_of))
        return {r[0] for r in cur.fetchall()}


def test_delisted_company_is_present_for_dates_before_it_delisted(conn, delisted_company):
    """The survivorship-bias test.

    A screen run as of November 2026 must include a company that failed in
    2027. Every screener that drops delisted companies gets this wrong, and the
    error always flatters the historical result.
    """
    assert delisted_company in _universe(conn, date(2026, 11, 1), utc(2030, 1, 1))


def test_delisted_company_is_absent_after_it_delisted(conn, delisted_company):
    assert delisted_company not in _universe(conn, date(2027, 6, 1), utc(2030, 1, 1))


def test_delisting_we_had_not_yet_learned_does_not_apply(conn, delisted_company):
    """Both axes matter. Asking 'as of 2026, for the date 2028' must not apply a
    delisting we only learned about in 2027."""
    assert delisted_company in _universe(conn, date(2028, 1, 1), utc(2026, 12, 1))


def test_company_with_no_filings_is_not_in_the_universe(conn, source):
    """Tracked but never reported is not screenable: there is nothing to screen on."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO instruments (isin) VALUES ('INE000TEST99') "
            "RETURNING instrument_id"
        )
        empty = cur.fetchone()[0]
    add_lifecycle(conn, empty, source, event="LISTED",
                  event_date=date(2020, 1, 1), known_from=utc(2020, 1, 1))
    assert empty not in _universe(conn, date(2026, 11, 1), utc(2030, 1, 1))


def test_filing_for_a_later_period_does_not_make_a_company_investable_earlier(
    conn, instrument, source
):
    """A Q3 filing does not retroactively put the company in a Q1 universe."""
    add_lifecycle(conn, instrument, source, event="LISTED",
                  event_date=date(2026, 1, 1), known_from=utc(2026, 1, 1))
    add_filing(conn, instrument, source, period_end=date(2026, 12, 31),
               filed_at=utc(2027, 1, 15), ref="q3")
    assert instrument not in _universe(conn, date(2026, 6, 1), utc(2030, 1, 1))
