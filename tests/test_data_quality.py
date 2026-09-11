"""Phase 17 exit criterion: a seeded corruption is caught by the checks.

Every test here injects a plausible, well-formed, correctly-stored, traceable
fact that happens to be wrong — the failure the rest of the system cannot
detect. Append-only, versioning and provenance all guard the STORE. Nothing
before this phase guarded the CONTENT.

The corruptions are chosen to look like real mistakes: a concept mapped to the
wrong line item, a sign error, a figure paired with the wrong period. Not
garbage — garbage is caught at the boundary. These pass validation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from conftest import add_fact, add_filing, utc
from investment_intelligence.ingest import rejections
from investment_intelligence.sources.base import Rejection

FY = 2025
KNOWN = utc(2025, 6, 1)


def _put(conn, instrument, source, filing, **items):
    for line_item, value in items.items():
        add_fact(conn, instrument, source, filing,
                 line_item=line_item.upper(), value=Decimal(str(value)),
                 fiscal_year=FY, period_type="ANNUAL", known_from=KNOWN,
                 period_start=date(FY - 1, 4, 1), period_end=date(FY, 3, 31))


@pytest.fixture
def filing(conn, instrument, source):
    return add_filing(conn, instrument, source, period_end=date(FY, 3, 31),
                      filed_at=KNOWN, ref="q")


def _findings(conn, check_code: str | None = None) -> list[dict]:
    with conn.cursor() as cur:
        if check_code:
            cur.execute("SELECT check_code, detail, observed, inputs "
                        "FROM quality_as_of(%s) WHERE check_code = %s",
                        (utc(2026, 1, 1), check_code))
        else:
            cur.execute("SELECT check_code, detail, observed, inputs "
                        "FROM quality_as_of(%s)", (utc(2026, 1, 1),))
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _coverage(conn) -> dict[str, tuple[int, int]]:
    with conn.cursor() as cur:
        cur.execute("SELECT check_code, evaluable, findings FROM quality_coverage(%s)",
                    (utc(2026, 1, 1),))
        return {code: (ev, f) for code, ev, f in cur.fetchall()}


# ---------------------------------------------------------------------------
# Sound data produces no findings — but over a non-zero number of periods
# ---------------------------------------------------------------------------

def test_a_sound_statement_produces_no_findings(conn, instrument, source, filing):
    _put(conn, instrument, source, filing,
         revenue=1000, net_profit=100, profit_before_tax=130, tax_expense=30,
         total_assets=1000, total_equity=500)
    assert _findings(conn) == []


def test_and_the_checks_actually_ran(conn, instrument, source, filing):
    """The distinction that makes a clean report meaningful.

    Zero findings over zero evaluable periods is an inert check, not clean
    data. The first quality run on real data reported zero TAX_IDENTITY
    findings and it took a hand-written query to learn whether that meant
    anything.
    """
    _put(conn, instrument, source, filing,
         revenue=1000, net_profit=100, profit_before_tax=130, tax_expense=30,
         total_assets=1000, total_equity=500)
    coverage = _coverage(conn)
    assert coverage["TAX_IDENTITY"] == (1, 0)
    assert coverage["EQUITY_EXCEEDS_ASSETS"] == (1, 0)
    assert coverage["NEGATIVE_REVENUE"] == (1, 0)


def test_an_inert_check_is_visible_as_inert(conn, instrument, source, filing):
    """With no balance sheet, the equity check cannot run. Coverage must say
    so rather than reporting it clean."""
    _put(conn, instrument, source, filing, revenue=1000, net_profit=100)
    evaluable, findings = _coverage(conn)["EQUITY_EXCEEDS_ASSETS"]
    assert (evaluable, findings) == (0, 0), "expected an inert check"


# ---------------------------------------------------------------------------
# Seeded corruptions
# ---------------------------------------------------------------------------

def test_a_mismapped_concept_breaks_the_tax_identity(conn, instrument, source, filing):
    """The corruption the concept map cannot catch itself.

    Suppose `TAX_EXPENSE` were mapped to a deferred-tax component rather than
    total tax expense. Every figure is well-formed and traceable; the identity
    pbt - tax = net profit is what notices.
    """
    _put(conn, instrument, source, filing,
         revenue=1000, net_profit=100, profit_before_tax=130,
         tax_expense=5,            # should be 30
         total_assets=1000, total_equity=500)
    found = _findings(conn, "TAX_IDENTITY")
    assert len(found) == 1
    assert found[0]["observed"] > Decimal("0.02")
    assert found[0]["inputs"], "a finding must name the facts it implicates"


def test_the_tax_identity_tolerates_minority_interests(conn, instrument, source, filing):
    """A small break is legitimate -- minority interests and discontinued
    operations genuinely break the identity. Flagging those would bury the
    real breaks in noise."""
    _put(conn, instrument, source, filing,
         revenue=1000, net_profit=99, profit_before_tax=130, tax_expense=30,
         total_assets=1000, total_equity=500)
    assert _findings(conn, "TAX_IDENTITY") == []


def test_equity_exceeding_assets_is_an_error(conn, instrument, source, filing):
    """Would require negative liabilities. No legitimate reading."""
    _put(conn, instrument, source, filing,
         revenue=1000, net_profit=100, total_assets=500, total_equity=900)
    found = _findings(conn, "EQUITY_EXCEEDS_ASSETS")
    assert len(found) == 1
    with conn.cursor() as cur:
        cur.execute("SELECT severity FROM quality_checks "
                    "WHERE check_code='EQUITY_EXCEEDS_ASSETS'")
        assert cur.fetchone()[0] == "ERROR"


def test_a_sign_flipped_revenue_is_caught(conn, instrument, source, filing):
    _put(conn, instrument, source, filing, revenue=-1000, net_profit=100)
    assert len(_findings(conn, "NEGATIVE_REVENUE")) == 1


def test_a_swapped_tax_ratio_is_caught(conn, instrument, source, filing):
    """Tax expense larger than profit before tax: what a swapped numerator and
    denominator looks like."""
    _put(conn, instrument, source, filing,
         revenue=1000, profit_before_tax=100, tax_expense=130, net_profit=-30)
    assert len(_findings(conn, "IMPLAUSIBLE_TAX_RATE")) == 1


def test_a_half_ingested_period_is_reported(conn, instrument, source, filing):
    """Revenue with no profit figure. Not wrong, but every profitability
    metric for that period is silently absent rather than visibly missing."""
    _put(conn, instrument, source, filing, revenue=1000, total_assets=1000)
    assert len(_findings(conn, "INCOMPLETE_PERIOD")) == 1


# ---------------------------------------------------------------------------
# Cross-currency validation: a second source, without a second source
# ---------------------------------------------------------------------------

def _put_currency(conn, instrument, source, filing, currency, **items):
    with conn.cursor() as cur:
        for line_item, value in items.items():
            cur.execute(
                "INSERT INTO financial_facts (instrument_id, basis, fiscal_year, "
                "period_type, line_item, period_start, period_end, value, currency, "
                "known_from, filing_id, source_id) VALUES "
                "(%s,'CONSOLIDATED',%s,'ANNUAL',%s,%s,%s,%s,%s,%s,%s,%s)",
                (instrument, FY, line_item.upper(), date(FY - 1, 4, 1),
                 date(FY, 3, 31), Decimal(str(value)), currency, KNOWN,
                 filing, source))


def test_consistent_dual_currency_reporting_passes(conn, instrument, source, filing):
    """Same story twice at one exchange rate: 1000 INR = 12 USD, 100 = 1.2."""
    _put_currency(conn, instrument, source, filing, "INR", revenue=1000, net_profit=100)
    _put_currency(conn, instrument, source, filing, "USD", revenue=12, net_profit=1.2)
    assert _findings(conn, "FX_INCONSISTENT") == []
    assert _coverage(conn)["FX_INCONSISTENT"][0] == 1, "the check must have run"


def test_a_mismatched_currency_pair_is_caught(conn, instrument, source, filing):
    """The corruption this catches: one of the two figures belongs to a
    different period, basis, or line item.

    Revenue implies 83.3 INR/USD; profit implies 200. Both figures are
    individually plausible. Nothing else in the system would notice, and this
    needs no second source -- the company told us the same story twice.
    """
    _put_currency(conn, instrument, source, filing, "INR", revenue=1000, net_profit=100)
    _put_currency(conn, instrument, source, filing, "USD", revenue=12, net_profit=0.5)
    found = _findings(conn, "FX_INCONSISTENT")
    assert len(found) == 1
    assert found[0]["observed"] > Decimal("0.05")
    assert len(found[0]["inputs"]) == 4, "must implicate all four figures"


def test_fx_check_needs_no_second_source(conn, instrument, source, filing):
    """Stated as a test because it is the interesting property: the only
    source we have is EDGAR, and cross-validation is still possible."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(DISTINCT source_id) FROM financial_facts")
        assert cur.fetchone()[0] <= 1


# ---------------------------------------------------------------------------
# Rejection classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reason,expected", [
    ("balance date matches no reported period end", "UNPLACEABLE_BALANCE_DATE"),
    ("unrecognised period span of 272 days", "UNRECOGNISED_PERIOD_SPAN"),
    ("companyfacts unavailable: HTTP 404", "SOURCE_UNAVAILABLE"),
    ("instrument is not a known SEC registrant", "NOT_A_REGISTRANT"),
    ("unknown reference: this source has no record of it", "UNKNOWN_REFERENCE"),
    ("same fact reported twice in one filing with different values: 1 vs 2",
     "DUPLICATE_IN_FILING"),
    ("value must be Decimal, got float", "INVALID_VALUE"),
    ("something nobody has written a rule for", "UNCLASSIFIED"),
])
def test_rejection_classification(reason, expected):
    assert rejections.classify(reason) == expected


def test_rejections_are_persisted_and_grouped(conn, instrument, source):
    """Phase 7 counted 372 rejections and discarded every reason. Grouping on
    the raw text would give 372 groups of one, because reasons carry values
    and dates -- the class is what turns a count into a decision."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ingestion_runs (source_id, kind) VALUES (%s,'FUNDAMENTALS') "
            "RETURNING run_id", (source,))
        run_id = cur.fetchone()[0]

    stored = rejections.record(conn, run_id, source, [
        Rejection(instrument_ref="A", detail="x", reason="unrecognised period span of 90 days"),
        Rejection(instrument_ref="B", detail="y", reason="unrecognised period span of 272 days"),
        Rejection(instrument_ref="C", detail="z", reason="companyfacts unavailable: HTTP 404"),
    ])
    assert stored == 3

    summary = {row["reason_class"]: row for row in rejections.summary(conn)}
    assert summary["UNRECOGNISED_PERIOD_SPAN"]["rejections"] == 2
    assert summary["UNRECOGNISED_PERIOD_SPAN"]["instruments"] == 2
    assert summary["SOURCE_UNAVAILABLE"]["rejections"] == 1
    assert rejections.unclassified(conn) == []


def test_an_unrecognised_reason_lands_in_unclassified(conn, instrument, source):
    """A growing UNCLASSIFIED bucket means the source started rejecting things
    for a new reason and nobody noticed."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ingestion_runs (source_id, kind) VALUES (%s,'FUNDAMENTALS') "
            "RETURNING run_id", (source,))
        run_id = cur.fetchone()[0]
    rejections.record(conn, run_id, source, [
        Rejection(instrument_ref="A", detail="x", reason="a brand new failure mode")])
    assert rejections.unclassified(conn) == ["a brand new failure mode"]


# ---------------------------------------------------------------------------
# Quality is itself as-of dated
# ---------------------------------------------------------------------------

def test_quality_can_be_assessed_for_a_past_date(conn, instrument, source):
    """"Was the data we were serving in 2020 sound?" is a question this
    product should be able to answer about itself.

    A corruption introduced and later corrected must still be visible as of a
    date in between.
    """
    bad = add_filing(conn, instrument, source, period_end=date(FY, 3, 31),
                     filed_at=utc(2025, 6, 1), ref="bad")
    _put(conn, instrument, source, bad,
         revenue=1000, net_profit=100, profit_before_tax=130, tax_expense=5)

    good = add_filing(conn, instrument, source, period_end=date(FY, 3, 31),
                      filed_at=utc(2026, 1, 1), ref="good")
    add_fact(conn, instrument, source, good, line_item="TAX_EXPENSE",
             value=Decimal("30"), fiscal_year=FY, period_type="ANNUAL",
             known_from=utc(2026, 1, 1),
             period_start=date(FY - 1, 4, 1), period_end=date(FY, 3, 31))

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM quality_as_of(%s) WHERE check_code='TAX_IDENTITY'",
                    (utc(2025, 9, 1),))
        assert cur.fetchone()[0] == 1, "the corruption was invisible as of when it existed"
        cur.execute("SELECT count(*) FROM quality_as_of(%s) WHERE check_code='TAX_IDENTITY'",
                    (utc(2026, 6, 1),))
        assert cur.fetchone()[0] == 0, "the correction was not picked up"
