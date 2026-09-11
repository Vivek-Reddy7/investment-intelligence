"""Phase 9 exit criteria, as tests.

    Every metric names its inputs, period and computation time.
    A screen run as of a past date excludes companies that had not yet listed
    and uses figures as they were then reported.

The second is the one that makes this a product rather than a screener. Its
failure mode is silent: a historical screen that quietly uses restated figures
returns a flattering, plausible answer and no error.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from conftest import add_fact, add_filing, add_lifecycle, utc
from investment_intelligence.analytics.screen import (
    Criterion,
    InvalidCriterion,
    explain,
    refresh,
    screen,
)

# Deliberately all in the past. An earlier version of this file used 2026/2027
# dates, which put the restatement ahead of the wall clock -- so `facts_as_of`
# defaulting to now() correctly did not see it, and the test failed for the
# right reason. Keeping the scenario historical means the default now() path is
# genuinely exercised instead of being accidentally equivalent to "before".
FY = 2025
BEFORE = utc(2025, 6, 1)     # figures as first reported
AFTER = utc(2026, 6, 1)      # after restatement, still before today


def _company(conn, source, isin: str, *, listed: date = date(2015, 4, 1)) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO instruments (isin) VALUES (%s) RETURNING instrument_id",
                    (isin,))
        instrument_id = cur.fetchone()[0]
        cur.execute("INSERT INTO tracked_instruments (instrument_id) VALUES (%s)",
                    (instrument_id,))
    add_lifecycle(conn, instrument_id, source, event="LISTED",
                  event_date=listed, known_from=utc(listed.year, listed.month, listed.day))
    return instrument_id


def _report(conn, instrument_id, source, *, known, ref, **items):
    """File one annual report carrying the given line items."""
    filing = add_filing(conn, instrument_id, source, period_end=date(FY, 3, 31),
                        filed_at=known, ref=ref)
    for line_item, value in items.items():
        add_fact(conn, instrument_id, source, filing,
                 line_item=line_item.upper(), value=Decimal(str(value)),
                 fiscal_year=FY, period_type="ANNUAL", known_from=known,
                 period_start=date(FY - 1, 4, 1), period_end=date(FY, 3, 31))
    return filing


@pytest.fixture
def solid(conn, source):
    """A healthy company: 10% margin, 20% ROE, low leverage."""
    instrument_id = _company(conn, source, "INE000SOLID1")
    _report(conn, instrument_id, source, known=BEFORE, ref="solid-fy26",
            revenue=1000, net_profit=100, total_assets=1000, total_equity=500,
            profit_before_tax=130, tax_expense=30, cf_operating=120)
    return instrument_id


# ---------------------------------------------------------------------------
# Metric correctness
# ---------------------------------------------------------------------------

def _metrics(conn, instrument_id, as_of=None) -> dict[str, Decimal]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT metric_code, value FROM metrics_as_of(%s) WHERE instrument_id = %s",
            (as_of or AFTER, instrument_id),
        )
        return dict(cur.fetchall())


def test_metrics_compute_correctly(conn, solid):
    m = _metrics(conn, solid)
    assert m["NET_MARGIN"] == Decimal("0.1")            # 100 / 1000
    assert m["ROE"] == Decimal("0.2")                   # 100 / 500
    assert m["ROA"] == Decimal("0.1")                   # 100 / 1000
    assert m["LIAB_TO_EQUITY"] == Decimal("1")          # (1000-500) / 500
    assert m["EQUITY_RATIO"] == Decimal("0.5")          # 500 / 1000
    assert m["CASH_CONVERSION"] == Decimal("1.2")       # 120 / 100
    assert round(m["EFFECTIVE_TAX"], 4) == Decimal("0.2308")  # 30 / 130


def test_growth_needs_a_prior_year(conn, source, solid):
    """Absent, not zero. A company with one year of history has no growth
    rate, and reporting 0% would be a fabrication."""
    assert "REVENUE_GROWTH" not in _metrics(conn, solid)


def test_growth_computes_across_two_years(conn, source):
    instrument_id = _company(conn, source, "INE000GROWS1")
    for fy, revenue, profit in ((FY - 1, 800, 80), (FY, 1000, 100)):
        filing = add_filing(conn, instrument_id, source,
                            period_end=date(fy, 3, 31), filed_at=BEFORE, ref=f"g{fy}")
        for item, value in (("REVENUE", revenue), ("NET_PROFIT", profit)):
            add_fact(conn, instrument_id, source, filing, line_item=item,
                     value=Decimal(value), fiscal_year=fy, period_type="ANNUAL",
                     known_from=BEFORE, period_start=date(fy - 1, 4, 1),
                     period_end=date(fy, 3, 31))
    m = _metrics(conn, instrument_id)
    assert m["REVENUE_GROWTH"] == Decimal("0.25")   # 1000/800 - 1
    assert m["PROFIT_GROWTH"] == Decimal("0.25")


# ---------------------------------------------------------------------------
# Undefined is absent, not a number
# ---------------------------------------------------------------------------

def test_return_on_negative_equity_is_omitted(conn, source):
    """An insolvent company does not have a meaningful ROE, and emitting one
    puts broken figures at the top of a ranking sorted by profitability."""
    instrument_id = _company(conn, source, "INE000NEGEQ1")
    _report(conn, instrument_id, source, known=BEFORE, ref="negeq",
            revenue=1000, net_profit=100, total_assets=1000, total_equity=-200)
    m = _metrics(conn, instrument_id)
    assert "ROE" not in m
    assert "LIAB_TO_EQUITY" not in m
    # Equity-to-assets IS meaningful with negative equity: it says insolvent.
    assert m["EQUITY_RATIO"] == Decimal("-0.2")


def test_margin_on_zero_revenue_is_omitted(conn, source):
    instrument_id = _company(conn, source, "INE000ZEROR1")
    _report(conn, instrument_id, source, known=BEFORE, ref="zeror",
            revenue=0, net_profit=-50, total_assets=100, total_equity=50)
    assert "NET_MARGIN" not in _metrics(conn, instrument_id)


def test_growth_from_a_loss_is_omitted(conn, source):
    """-100 to +50 is not growth of -150%."""
    instrument_id = _company(conn, source, "INE000LOSSB1")
    for fy, profit in ((FY - 1, -100), (FY, 50)):
        filing = add_filing(conn, instrument_id, source, period_end=date(fy, 3, 31),
                            filed_at=BEFORE, ref=f"l{fy}")
        add_fact(conn, instrument_id, source, filing, line_item="NET_PROFIT",
                 value=Decimal(profit), fiscal_year=fy, period_type="ANNUAL",
                 known_from=BEFORE, period_start=date(fy - 1, 4, 1),
                 period_end=date(fy, 3, 31))
    assert "PROFIT_GROWTH" not in _metrics(conn, instrument_id)


def test_cash_conversion_against_a_loss_is_omitted(conn, source):
    instrument_id = _company(conn, source, "INE000CASHL1")
    _report(conn, instrument_id, source, known=BEFORE, ref="cashl",
            revenue=1000, net_profit=-100, cf_operating=50,
            total_assets=1000, total_equity=500)
    assert "CASH_CONVERSION" not in _metrics(conn, instrument_id)


def test_a_metric_is_never_computed_across_currencies(conn, source):
    """Infosys reports revenue in USD and INR. A margin taking INR profit over
    USD revenue is a plausible number that means nothing, so currency is part
    of the grouping key."""
    instrument_id = _company(conn, source, "INE000MIXED1")
    filing = add_filing(conn, instrument_id, source, period_end=date(FY, 3, 31),
                        filed_at=BEFORE, ref="mixed")
    with conn.cursor() as cur:
        for item, value, currency in (("REVENUE", 1000, "INR"),
                                      ("NET_PROFIT", 12, "USD")):
            cur.execute(
                "INSERT INTO financial_facts (instrument_id, basis, fiscal_year, "
                "period_type, line_item, period_start, period_end, value, currency, "
                "known_from, filing_id, source_id) VALUES (%s,'CONSOLIDATED',%s,"
                "'ANNUAL',%s,%s,%s,%s,%s,%s,%s,%s)",
                (instrument_id, FY, item, date(FY - 1, 4, 1), date(FY, 3, 31),
                 Decimal(value), currency, BEFORE, filing, source),
            )
    # Neither currency has both inputs, so no margin exists in either.
    assert "NET_MARGIN" not in _metrics(conn, instrument_id)


# ---------------------------------------------------------------------------
# Provenance — the exit criterion
# ---------------------------------------------------------------------------

def test_every_metric_names_its_inputs(conn, solid):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT metric_code, inputs FROM metrics_as_of(%s) WHERE instrument_id = %s",
            (AFTER, solid),
        )
        rows = cur.fetchall()
    assert rows
    for metric_code, inputs in rows:
        assert inputs, metric_code
        assert None not in inputs, metric_code


def test_inputs_resolve_to_real_facts_and_their_documents(conn, solid):
    """The full chain: metric -> facts -> filing -> source -> licence note."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT inputs FROM metrics_as_of(%s) "
            "WHERE instrument_id = %s AND metric_code = 'NET_MARGIN'",
            (AFTER, solid),
        )
        inputs = cur.fetchone()[0]

    chain = explain(conn, inputs)
    assert {row["line_item"] for row in chain} == {"NET_PROFIT", "REVENUE"}
    for row in chain:
        assert row["source_ref"]
        assert row["licence_note"]
        assert row["known_from"]


def test_materialised_values_record_when_they_were_computed(conn, solid):
    refresh(conn)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT computed_at, as_of FROM metric_values WHERE instrument_id = %s "
            "LIMIT 1", (solid,))
        computed_at, as_of = cur.fetchone()
    assert computed_at is not None and as_of is not None


def test_the_materialised_view_agrees_with_the_function(conn, solid):
    """One definition, two paths. If these diverge, a screen gives different
    answers depending on the date asked about -- which looks like the feature
    working."""
    refresh(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM metric_values mv
            FULL JOIN metrics_as_of(now()) f
              ON  f.instrument_id = mv.instrument_id
              AND f.metric_code   = mv.metric_code
              AND f.fiscal_year   = mv.fiscal_year
              AND f.currency      = mv.currency
            WHERE mv.value IS DISTINCT FROM f.value
            """
        )
        assert cur.fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Screening, and the as-of behaviour that is the point
# ---------------------------------------------------------------------------

PROFITABLE = [Criterion("NET_MARGIN", "gte", Decimal("0.05"))]


def test_a_live_screen_finds_a_qualifying_company(conn, solid):
    refresh(conn)
    found = screen(conn, PROFITABLE, fiscal_year=FY)
    assert solid in {r.instrument_id for r in found}


def test_a_screen_excludes_a_company_that_fails_a_criterion(conn, source, solid):
    thin = _company(conn, source, "INE000THIN01")
    _report(conn, thin, source, known=BEFORE, ref="thin",
            revenue=1000, net_profit=10, total_assets=1000, total_equity=500)
    refresh(conn)
    found = {r.instrument_id for r in screen(conn, PROFITABLE, fiscal_year=FY)}
    assert solid in found and thin not in found


def test_a_screen_requires_every_criterion(conn, source, solid):
    """A company missing one of the screened metrics must fail, not slip
    through on a NULL. This is why each criterion is its own EXISTS."""
    partial = _company(conn, source, "INE000PARTL1")
    _report(conn, partial, source, known=BEFORE, ref="partl",
            revenue=1000, net_profit=200)   # no balance sheet -> no ROE
    refresh(conn)
    found = {r.instrument_id for r in screen(
        conn,
        [Criterion("NET_MARGIN", "gte", Decimal("0.05")),
         Criterion("ROE", "gte", Decimal("0.1"))],
        fiscal_year=FY)}
    assert solid in found
    assert partial not in found, "a missing metric let a company through"


def test_a_historical_screen_uses_the_then_reported_figure(conn, source):
    """The product.

    Profit first reported as 100 on a revenue of 1000 (10% margin), later
    restated to 20 (2%). A screen for margin >= 5% must include the company
    when run as of the earlier date and exclude it when run today.
    """
    instrument_id = _company(conn, source, "INE000RESTA1")
    _report(conn, instrument_id, source, known=BEFORE, ref="orig",
            revenue=1000, net_profit=100, total_assets=1000, total_equity=500)
    _report(conn, instrument_id, source, known=AFTER, ref="restated",
            revenue=1000, net_profit=20, total_assets=1000, total_equity=500)
    refresh(conn)

    then = {r.instrument_id for r in screen(
        conn, PROFITABLE, fiscal_year=FY, as_of=utc(2025, 9, 1),
        on=date(2025, 9, 1))}
    now = {r.instrument_id for r in screen(conn, PROFITABLE, fiscal_year=FY)}

    assert instrument_id in then, "historical screen used the restated figure"
    assert instrument_id not in now


def test_a_historical_screen_excludes_a_company_that_had_not_yet_reported(conn, source):
    """No lookahead in the universe, not just in the figures."""
    late = _company(conn, source, "INE000LATE01", listed=date(2025, 1, 1))
    _report(conn, late, source, known=AFTER, ref="late",
            revenue=1000, net_profit=100, total_assets=1000, total_equity=500)

    early = {r.instrument_id for r in screen(
        conn, PROFITABLE, fiscal_year=FY, as_of=utc(2025, 9, 1),
        on=date(2025, 9, 1))}
    assert late not in early


def test_a_delisted_company_still_appears_in_a_past_screen(conn, source, solid):
    """Survivorship bias, at the screening layer."""
    add_lifecycle(conn, solid, source, event="DELISTED",
                  event_date=date(2027, 3, 31), known_from=utc(2027, 3, 31))
    past = {r.instrument_id for r in screen(
        conn, PROFITABLE, fiscal_year=FY, as_of=utc(2025, 9, 1),
        on=date(2025, 9, 1))}
    assert solid in past


def test_a_screen_result_carries_provenance(conn, solid):
    refresh(conn)
    result = next(r for r in screen(conn, PROFITABLE, fiscal_year=FY)
                  if r.instrument_id == solid)
    assert result.inputs["NET_MARGIN"]
    assert explain(conn, result.inputs["NET_MARGIN"])


# ---------------------------------------------------------------------------
# Input validation — a screen is user input reaching a WHERE clause
# ---------------------------------------------------------------------------

def test_an_unknown_operator_is_refused():
    with pytest.raises(InvalidCriterion, match="operator"):
        Criterion("ROE", "; DROP TABLE financial_facts; --", Decimal("1"))


def test_a_float_threshold_is_refused():
    with pytest.raises(InvalidCriterion, match="Decimal"):
        Criterion("ROE", "gt", 0.1)


def test_an_unknown_metric_is_refused_loudly(conn):
    """Rather than returning an empty screen, which reads as 'no matches'."""
    with pytest.raises(InvalidCriterion, match="unknown metric"):
        screen(conn, [Criterion("MADE_UP", "gt", Decimal("1"))], fiscal_year=FY)


def test_an_empty_screen_is_refused(conn):
    with pytest.raises(InvalidCriterion):
        screen(conn, [], fiscal_year=FY)
