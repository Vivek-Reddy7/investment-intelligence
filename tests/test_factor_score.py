"""Factor score: percentile ranking, point-in-time momentum, and the
honest handling of partial factor coverage.

The point-in-time cases are the ones that matter most, for the usual reason
in this codebase: a factor score that can see the future would look exactly
like a working one until someone checks.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from conftest import add_fact, add_lifecycle, utc
from investment_intelligence.analytics.factor_score import (
    MOMENTUM_TRADING_DAYS,
    _latest_metrics,
    _momentum,
    _percentile_ranks,
    compute_scores,
)
from investment_intelligence.ingest.price_writer import write_bars
from investment_intelligence.sources.prices import PriceBar


# ---------------------------------------------------------------------------
# _percentile_ranks -- pure function, no database
# ---------------------------------------------------------------------------

def test_percentile_ranks_orders_low_to_high():
    ranks = _percentile_ranks({1: Decimal("10"), 2: Decimal("30"), 3: Decimal("20")})
    assert ranks[1] == 0.0    # lowest value
    assert ranks[3] == 0.5    # middle
    assert ranks[2] == 1.0    # highest value


def test_percentile_ranks_single_instrument_is_the_median():
    assert _percentile_ranks({1: Decimal("42")}) == {1: 0.5}


def test_percentile_ranks_empty_input():
    assert _percentile_ranks({}) == {}


def test_percentile_ranks_excludes_nothing_it_was_not_given():
    # The caller is responsible for excluding instruments missing a factor
    # BEFORE calling this -- it ranks whatever dict it receives and has no
    # way to know a name was left out on purpose vs. by accident. This test
    # exists to pin that division of responsibility.
    ranks = _percentile_ranks({1: Decimal("5")})
    assert set(ranks) == {1}


# ---------------------------------------------------------------------------
# Point-in-time momentum
# ---------------------------------------------------------------------------

@pytest.fixture
def price_source(conn):
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


@pytest.fixture
def priced_instrument(conn, price_source) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO instruments (isin) VALUES ('INE000FACTR1') "
                     "RETURNING instrument_id")
        return cur.fetchone()[0]


class _FakeSource:
    source_id = "TEST_PRICES"
    key_scheme = "US_TICKER"


def _write_daily_bars(conn, instrument_id: int, start: date, n_days: int,
                       start_close: Decimal, daily_step: Decimal) -> None:
    """n_days consecutive calendar-day bars (weekends included -- the point
    here is testing the window logic, not modelling a real trading calendar),
    closing price walking by `daily_step` each day."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO instrument_external_ids (instrument_id, scheme, value, source_id) "
            "VALUES (%s, 'US_TICKER', 'TEST', 'TEST_PRICES') ON CONFLICT DO NOTHING",
            (instrument_id,),
        )
    bars = []
    close = start_close
    day = start
    for _ in range(n_days):
        bars.append(PriceBar(instrument_ref="TEST", day=day, open=close, high=close + 1,
                              low=close - 1, close=close, volume=1000))
        close += daily_step
        day = date.fromordinal(day.toordinal() + 1)
    write_bars(conn, _FakeSource(), "TEST", bars)


def test_momentum_none_with_insufficient_history(conn, priced_instrument):
    # Fewer bars than the window needs -- must not silently compute a return
    # over a shorter, undisclosed period.
    _write_daily_bars(conn, priced_instrument, date(2026, 1, 1),
                       n_days=MOMENTUM_TRADING_DAYS - 10,
                       start_close=Decimal("100"), daily_step=Decimal("0"))
    as_of = date.fromordinal(date(2026, 1, 1).toordinal() + (MOMENTUM_TRADING_DAYS - 11))
    assert _momentum(conn, priced_instrument, as_of) is None


def test_momentum_computed_with_exactly_enough_history(conn, priced_instrument):
    _write_daily_bars(conn, priced_instrument, date(2026, 1, 1),
                       n_days=MOMENTUM_TRADING_DAYS + 1,
                       start_close=Decimal("100"), daily_step=Decimal("0"))
    as_of = date.fromordinal(date(2026, 1, 1).toordinal() + MOMENTUM_TRADING_DAYS)
    # Flat price throughout -> exactly zero return, not None.
    result = _momentum(conn, priced_instrument, as_of)
    assert result == Decimal("0")


def test_momentum_ignores_bars_after_as_of(conn, priced_instrument):
    """The point-in-time guarantee this whole module exists for: a bar dated
    after as_of must not leak into the computation, even though it is sitting
    right there in the same table."""
    _write_daily_bars(conn, priced_instrument, date(2026, 1, 1),
                       n_days=MOMENTUM_TRADING_DAYS + 1,
                       start_close=Decimal("100"), daily_step=Decimal("0"))
    as_of = date.fromordinal(date(2026, 1, 1).toordinal() + MOMENTUM_TRADING_DAYS)
    before = _momentum(conn, priced_instrument, as_of)

    # A huge price jump the day AFTER as_of. If the function is wrong and
    # scans by "latest N rows" instead of "rows on or before as_of", this
    # future bar would corrupt the already-computed answer above.
    future_day = date.fromordinal(as_of.toordinal() + 1)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO price_bars (instrument_id, day, open, high, low, close, "
            "volume, source_id) VALUES (%s, %s, 900, 950, 890, 940, 1000, 'TEST_PRICES')",
            (priced_instrument, future_day),
        )
    after = _momentum(conn, priced_instrument, as_of)
    assert after == before


# ---------------------------------------------------------------------------
# compute_scores -- honest handling of partial coverage
# ---------------------------------------------------------------------------

@pytest.fixture
def instrument_with_fundamentals(conn, source, price_source) -> int:
    """A company with both price history and a filed annual report, so its
    factor score should have all three components."""
    instrument_id = None
    with conn.cursor() as cur:
        cur.execute("INSERT INTO instruments (isin) VALUES ('INE000FULL01') "
                     "RETURNING instrument_id")
        instrument_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO instrument_external_ids (instrument_id, scheme, value, source_id) "
            "VALUES (%s, 'US_TICKER', 'TEST', 'TEST_PRICES')",
            (instrument_id,),
        )
    add_lifecycle(conn, instrument_id, source, event="LISTED",
                  event_date=date(2015, 1, 1), known_from=utc(2015, 1, 1))
    return instrument_id


def test_compute_scores_drops_instrument_with_zero_factors(conn, source, price_source):
    """An instrument with no price history and no fundamentals must not
    appear in the output at all -- a "0 out of 3" score would be
    indistinguishable from a genuine bottom score."""
    with conn.cursor() as cur:
        cur.execute("INSERT INTO instruments (isin) VALUES ('INE000EMPTY1') "
                     "RETURNING instrument_id")
        empty_id = cur.fetchone()[0]

    scores = compute_scores(conn, [empty_id], date(2026, 1, 1))
    assert scores == []


def test_compute_scores_marks_partial_coverage_honestly(conn, priced_instrument):
    """Price-only, no fundamentals filed: the composite must be built from
    the one available factor, and factors_available must say so rather than
    implying a rounded 3-factor assessment."""
    _write_daily_bars(conn, priced_instrument, date(2026, 1, 1),
                       n_days=MOMENTUM_TRADING_DAYS + 1,
                       start_close=Decimal("100"), daily_step=Decimal("1"))
    as_of = date.fromordinal(date(2026, 1, 1).toordinal() + MOMENTUM_TRADING_DAYS)

    scores = compute_scores(conn, [priced_instrument], as_of)
    assert len(scores) == 1
    assert scores[0].factors["factors_available"] == 1
    assert set(scores[0].factors["components"]) == {"momentum"}
