"""Backtesting: the correlation math on hand-checkable series, and the
forward-return lookup against real price_bars.

Deliberately not testing run() end to end against a full historical
scoring pass here -- that would mean re-deriving fundamentals fixtures
across multiple fiscal years just to exercise this module, and
combined_score.compute_scores already has its own tests covering the
scoring logic this module reuses unmodified. What is specific to THIS
module -- the correlation arithmetic and the forward-looking price lookup
-- gets its own direct tests instead.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from investment_intelligence.analytics.backtest import (
    BacktestPoint,
    _forward_return,
    _ranks,
    information_coefficient,
)
from investment_intelligence.ingest.price_writer import write_bars
from investment_intelligence.sources.prices import PriceBar


def D(x) -> Decimal:
    return Decimal(str(x))


# ---------------------------------------------------------------------------
# _ranks -- average ranks, ties split evenly
# ---------------------------------------------------------------------------

def test_ranks_strictly_increasing_values():
    assert _ranks([10.0, 20.0, 30.0]) == [0.0, 1.0, 2.0]


def test_ranks_are_order_independent_of_input_position():
    assert _ranks([30.0, 10.0, 20.0]) == [2.0, 0.0, 1.0]


def test_tied_values_get_the_average_of_their_ranks():
    # Two values tied for the bottom two ranks (0 and 1) each get 0.5.
    assert _ranks([5.0, 5.0, 9.0]) == [0.5, 0.5, 2.0]


def test_all_tied_gives_every_element_the_same_middle_rank():
    assert _ranks([7.0, 7.0, 7.0, 7.0]) == [1.5, 1.5, 1.5, 1.5]


# ---------------------------------------------------------------------------
# information_coefficient -- hand-checkable correlation cases
# ---------------------------------------------------------------------------

def _points(scores: list[float], returns: list[float]) -> list[BacktestPoint]:
    return [
        BacktestPoint(instrument_id=i, as_of=date(2020, 1, 1), composite_score=D(s),
                      forward_days=180, forward_return=D(r))
        for i, (s, r) in enumerate(zip(scores, returns))
    ]


def test_perfect_agreement_is_ic_of_one():
    # Higher score, higher return, in exactly the same order -- the ranking
    # would have been a perfect guide.
    points = _points([0.1, 0.5, 0.9], [-0.10, 0.05, 0.30])
    result = information_coefficient(points)
    assert result["ic"] == pytest.approx(1.0)
    assert result["n"] == 3


def test_perfect_inversion_is_ic_of_negative_one():
    # Highest score, WORST return -- the ranking would have actively misled.
    points = _points([0.1, 0.5, 0.9], [0.30, 0.05, -0.10])
    result = information_coefficient(points)
    assert result["ic"] == pytest.approx(-1.0)


def test_no_relationship_is_ic_near_zero():
    # Scores monotonic, returns deliberately scrambled relative to them.
    points = _points([0.1, 0.3, 0.5, 0.7, 0.9], [0.02, -0.05, 0.02, -0.05, 0.02])
    result = information_coefficient(points)
    # Not asserting an exact value -- the point is that it is clearly not
    # near +-1, i.e. the ranking has no real ordering power here.
    assert -0.5 < result["ic"] < 0.5


def test_fewer_than_three_points_refuses_rather_than_computes_a_meaningless_number():
    result = information_coefficient(_points([0.1, 0.9], [0.0, 0.0]))
    assert result["ic"] is None
    assert result["n"] == 2


def test_zero_variance_in_returns_is_undefined_not_zero():
    # Every instrument had the exact same return -- correlation is
    # mathematically undefined here, and reporting 0.0 would misleadingly
    # read as "no relationship" rather than "nothing to relate to".
    points = _points([0.1, 0.5, 0.9], [0.05, 0.05, 0.05])
    result = information_coefficient(points)
    assert result["ic"] is None


# ---------------------------------------------------------------------------
# _forward_return -- against real price_bars
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
        cur.execute("INSERT INTO instruments (isin) VALUES ('INE000BTEST1') "
                     "RETURNING instrument_id")
        instrument_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO instrument_external_ids (instrument_id, scheme, value, source_id) "
            "VALUES (%s, 'US_TICKER', 'TEST', 'TEST_PRICES')",
            (instrument_id,),
        )
    bars = [
        PriceBar(instrument_ref="TEST", day=date(2020, 1, 1), open=D(100), high=D(101),
                 low=D(99), close=D(100), volume=1000),
        PriceBar(instrument_ref="TEST", day=date(2020, 7, 1), open=D(120), high=D(121),
                 low=D(119), close=D(120), volume=1000),
    ]
    write_bars(conn, _FakeSource(), "TEST", bars)
    return instrument_id


def test_forward_return_is_the_actual_percentage_change(conn, priced_instrument):
    result = _forward_return(conn, priced_instrument, date(2020, 1, 1), forward_days=182)
    assert result == D("0.20")   # 100 -> 120 is +20%


def test_forward_return_none_when_the_nearest_bar_is_too_far_from_the_target(conn, priced_instrument):
    # forward_days=3650 targets 2030 -- ten years past this instrument's
    # last bar (2020-07-01). A naive "nearest bar on or before target" would
    # silently return the 2020-07-01 close as though it were the 3650-day
    # price. The tolerance check must refuse instead.
    result = _forward_return(conn, priced_instrument, date(2020, 1, 1), forward_days=3650)
    assert result is None


def test_forward_return_tolerates_an_ordinary_weekend_gap(conn):
    """A target date that lands on a weekend, with the nearest real trading
    day just a day or two later, must still resolve -- the tolerance exists
    for exactly this, not just to be a no-op."""
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
        cur.execute("INSERT INTO instruments (isin) VALUES ('INE000BTEST2') "
                     "RETURNING instrument_id")
        instrument_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO instrument_external_ids (instrument_id, scheme, value, source_id) "
            "VALUES (%s, 'US_TICKER', 'TEST', 'TEST_PRICES')",
            (instrument_id,),
        )
    bars = [
        PriceBar(instrument_ref="TEST", day=date(2020, 1, 1), open=D(100), high=D(101),
                 low=D(99), close=D(100), volume=1000),
        # Target will be 2020-01-04 (a Saturday); nearest bar is 2020-01-03,
        # one day short -- well inside the tolerance.
        PriceBar(instrument_ref="TEST", day=date(2020, 1, 3), open=D(110), high=D(111),
                 low=D(109), close=D(110), volume=1000),
    ]
    write_bars(conn, _FakeSource(), "TEST", bars)

    result = _forward_return(conn, instrument_id, date(2020, 1, 1), forward_days=3)
    assert result == D("0.10")   # 100 -> 110
