"""Risk metrics: volatility, drawdown, historical VaR -- on hand-checkable
series, and the point-in-time guarantee against the database.

Closes are built FROM a chosen list of exact returns (each close = prior
close * (1+r)), not the other way around -- reconstructing returns from an
arbitrary close series is never clean, because an up-move and the down-move
that reverses it have different denominators. Generating closes from known
returns instead means the resulting daily returns are exactly the values
chosen, so the variance/percentile arithmetic can be verified by hand
rather than trusted.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from investment_intelligence.analytics.risk import (
    _daily_returns,
    annualized_volatility,
    compute,
    historical_var_95,
    max_drawdown,
)
from investment_intelligence.ingest.price_writer import write_bars
from investment_intelligence.sources.prices import PriceBar


def D(x) -> Decimal:
    return Decimal(str(x))


def _closes_from_returns(start: Decimal, returns: list[Decimal]) -> list[Decimal]:
    closes = [start]
    for r in returns:
        closes.append(closes[-1] * (1 + r))
    return closes


# ---------------------------------------------------------------------------
# _daily_returns
# ---------------------------------------------------------------------------

def test_daily_returns_reconstructs_the_exact_generating_returns():
    chosen = [D("0.05"), D("-0.10"), D("0.02")]
    closes = _closes_from_returns(D(100), chosen)
    assert _daily_returns(closes) == chosen


def test_daily_returns_empty_with_one_close():
    assert _daily_returns([D(100)]) == []


# ---------------------------------------------------------------------------
# annualized_volatility
# ---------------------------------------------------------------------------

def test_flat_series_has_zero_volatility():
    closes = _closes_from_returns(D(100), [D(0)] * 10)
    assert annualized_volatility(closes) == D(0)


def test_more_volatile_series_has_higher_annualized_volatility():
    calm = _closes_from_returns(D(100), [D("0.001"), D("-0.001")] * 10)
    wild = _closes_from_returns(D(100), [D("0.05"), D("-0.05")] * 10)
    assert annualized_volatility(wild) > annualized_volatility(calm)


def test_volatility_none_with_fewer_than_two_returns():
    assert annualized_volatility([D(100)]) is None
    assert annualized_volatility([D(100), D(101)]) is None  # exactly 1 return


# ---------------------------------------------------------------------------
# max_drawdown -- hand-verified exactly
# ---------------------------------------------------------------------------

def test_max_drawdown_exact_value():
    # peak sequence: 100, 120, 120, 120, 120, 130
    # drawdown at each point: 0, 0, (90-120)/120, (110-120)/120, (80-120)/120, 0
    #                        = 0, 0, -0.25,        -0.0833...,   -0.3333...,  0
    # worst = -1/3 exactly
    closes = [D(100), D(120), D(90), D(110), D(80), D(130)]
    result = max_drawdown(closes)
    assert result == pytest.approx(Decimal(-1) / Decimal(3))


def test_max_drawdown_zero_for_a_monotonically_rising_series():
    closes = [D(100), D(110), D(120), D(130)]
    assert max_drawdown(closes) == D(0)


def test_max_drawdown_is_from_the_running_peak_not_just_start_to_finish():
    # Ends higher than it started, but fell hard from an interim peak --
    # start-to-finish would wrongly say "no drawdown happened".
    closes = [D(100), D(200), D(50), D(300)]
    result = max_drawdown(closes)
    assert result == pytest.approx(Decimal(-0.75))   # (50-200)/200


def test_max_drawdown_none_with_a_single_close():
    assert max_drawdown([D(100)]) is None


# ---------------------------------------------------------------------------
# historical_var_95
# ---------------------------------------------------------------------------

def test_var95_picks_the_5th_percentile_of_generated_returns():
    # 20 exact, distinct returns, evenly spaced -- sorted ascending they are
    # exactly -0.19, -0.17, ..., 0.19. Nearest-rank 5th percentile index
    # into 20 sorted values is int(20*0.05) = 1, the second-smallest: -0.17.
    returns = [D(f"{(-19 + 2 * i) / 100:.2f}") for i in range(20)]
    closes = _closes_from_returns(D(1000), returns)
    result = historical_var_95(closes)
    assert result == D("-0.17")


def test_var95_none_with_fewer_than_20_returns():
    returns = [D("0.01")] * 10
    closes = _closes_from_returns(D(100), returns)
    assert historical_var_95(closes) is None


# ---------------------------------------------------------------------------
# compute -- point-in-time against real price_bars
# ---------------------------------------------------------------------------

class _FakeSource:
    source_id = "TEST_PRICES"
    key_scheme = "US_TICKER"


@pytest.fixture
def priced_instrument(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sources (source_id, name, kind, licence_note,
                                 redistributable, verified_on)
            VALUES ('TEST_PRICES', 'Test price source', 'MARKET_DATA_VENDOR',
                    'Test fixture. Not a real licence position.', false, %s)
            ON CONFLICT (source_id) DO NOTHING
            """,
            (date.today(),),
        )
        cur.execute("INSERT INTO instruments (isin) VALUES ('INE000RISK01') "
                     "RETURNING instrument_id")
        instrument_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO instrument_external_ids (instrument_id, scheme, value, source_id) "
            "VALUES (%s, 'US_TICKER', 'TEST', 'TEST_PRICES')",
            (instrument_id,),
        )
    return instrument_id


def test_compute_none_with_no_price_history(conn):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO instruments (isin) VALUES ('INE000RISK02') "
                     "RETURNING instrument_id")
        empty_id = cur.fetchone()[0]
    assert compute(conn, empty_id, date(2026, 1, 1)) is None


def test_compute_ignores_bars_after_as_of(conn, priced_instrument):
    """The same guarantee every point-in-time module in this project
    carries: a future bar must not change a metric computed as of a past
    date."""
    closes = _closes_from_returns(D(100), [D("0.01"), D("-0.02")] * 15)
    bars, day = [], date(2024, 1, 1)
    for close in closes:
        bars.append(PriceBar(instrument_ref="TEST", day=day, open=close, high=close + 1,
                              low=close - 1, close=close, volume=1000))
        day = date.fromordinal(day.toordinal() + 1)
    write_bars(conn, _FakeSource(), "TEST", bars)

    as_of = date.fromordinal(date(2024, 1, 1).toordinal() + len(closes) - 1)
    before = compute(conn, priced_instrument, as_of)

    future_day = date.fromordinal(as_of.toordinal() + 1)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO price_bars (instrument_id, day, open, high, low, close, "
            "volume, source_id) VALUES (%s, %s, 500, 550, 490, 540, 1000, 'TEST_PRICES')",
            (priced_instrument, future_day),
        )
    after = compute(conn, priced_instrument, as_of)
    assert after == before
