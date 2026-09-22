"""Technical indicators: the math, on hand-checkable series, and the
point-in-time guarantee against the database.

The math tests use series simple enough to verify by hand rather than by
trusting the function under test -- an all-gains RSI must be exactly 100,
not "close to 100 in a plausible way".
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from investment_intelligence.analytics.technicals import (
    LOOKBACK_SESSIONS,
    bollinger,
    compute,
    compute_and_store,
    macd,
    rolling_range,
    rsi_14,
    sma,
    volume_ratio,
)
from investment_intelligence.ingest.price_writer import write_bars
from investment_intelligence.sources.prices import PriceBar


def D(x) -> Decimal:
    return Decimal(str(x))


# ---------------------------------------------------------------------------
# sma
# ---------------------------------------------------------------------------

def test_sma_exact_window():
    assert sma([D(1), D(2), D(3), D(4), D(5)], 5) == D(3)


def test_sma_uses_the_trailing_window_only():
    # 6 values, window 5: must average the LAST 5, not the first 5.
    assert sma([D(100), D(1), D(2), D(3), D(4), D(5)], 5) == D(3)


def test_sma_none_with_insufficient_history():
    assert sma([D(1), D(2)], 5) is None


# ---------------------------------------------------------------------------
# rsi_14
# ---------------------------------------------------------------------------

def test_rsi_all_gains_is_100():
    # Strictly increasing by 1 each session, 15 closes (14 changes, all up).
    closes = [D(i) for i in range(100, 115)]
    assert rsi_14(closes) == D(100)


def test_rsi_all_losses_is_0():
    closes = [D(i) for i in range(115, 100, -1)]
    assert rsi_14(closes) == D(0)


def test_rsi_flat_series_returns_100_by_the_zero_loss_convention():
    # No gains, no losses at all -> avg_gain == avg_loss == 0 -> the
    # avg_loss == 0 branch fires and returns 100. That is the standard
    # convention (avoids a division by zero), not "50 for neutral" the way
    # a reader might guess. Pinning the actual behaviour rather than the
    # intuitive-sounding one, so a future change to this edge case is a
    # deliberate decision, not a surprise.
    closes = [D(50)] * 15
    assert rsi_14(closes) == D(100)


def test_rsi_none_with_insufficient_history():
    assert rsi_14([D(i) for i in range(10)]) is None


# ---------------------------------------------------------------------------
# macd
# ---------------------------------------------------------------------------

def test_macd_none_with_insufficient_history():
    line, signal, hist = macd([D(i) for i in range(20)])
    assert line is None and signal is None and hist is None


def test_macd_histogram_equals_line_minus_signal():
    # A trending series long enough to seed both EMAs and the signal line.
    closes = [D(100) + D(i) * D("0.3") for i in range(60)]
    line, signal, hist = macd(closes)
    assert line is not None
    assert hist == line - signal


def test_macd_flat_series_is_zero_everywhere():
    closes = [D(100)] * 60
    line, signal, hist = macd(closes)
    assert line == D(0)
    assert signal == D(0)
    assert hist == D(0)


# ---------------------------------------------------------------------------
# bollinger
# ---------------------------------------------------------------------------

def test_bollinger_flat_series_has_zero_width_and_no_percent_b():
    closes = [D(100)] * 20
    upper, lower, percent_b = bollinger(closes)
    assert upper == lower == D(100)
    assert percent_b is None   # division by zero band width, correctly refused


def test_bollinger_close_at_the_mean_is_roughly_midband():
    # Alternating +1/-1 around 100, 20 values, ending back at the mean.
    closes = [D(100) + (D(1) if i % 2 == 0 else D(-1)) for i in range(19)] + [D(100)]
    upper, lower, percent_b = bollinger(closes)
    assert upper > D(100) > lower
    assert D("0.4") < percent_b < D("0.6")


def test_bollinger_none_with_insufficient_history():
    assert bollinger([D(1)] * 5) == (None, None, None)


# ---------------------------------------------------------------------------
# volume_ratio
# ---------------------------------------------------------------------------

def test_volume_ratio_double_average_is_two():
    volumes = [1000] * 19 + [2000]   # today is 2x the flat baseline
    # baseline average includes today: (19*1000 + 2000)/20 = 1050
    ratio = volume_ratio(volumes, n=20)
    assert ratio == D(2000) / D(1050)


def test_volume_ratio_none_with_insufficient_history():
    assert volume_ratio([1000] * 5, n=20) is None


# ---------------------------------------------------------------------------
# rolling_range / breakout
# ---------------------------------------------------------------------------

def test_breakout_up_detected():
    highs = [D(100)] * 20 + [D(110)]
    lows = [D(90)] * 20 + [D(95)]
    closes = [D(95)] * 20 + [D(111)]   # closes above the prior 20-day high
    result = rolling_range(highs, lows, closes, 20)
    assert result["breakout"] == "BREAKOUT_UP"
    assert result["prior_high"] == D(100)


def test_breakout_down_detected():
    highs = [D(100)] * 20 + [D(90)]
    lows = [D(90)] * 20 + [D(80)]
    closes = [D(95)] * 20 + [D(85)]   # closes below the prior 20-day low
    result = rolling_range(highs, lows, closes, 20)
    assert result["breakout"] == "BREAKOUT_DOWN"
    assert result["prior_low"] == D(90)


def test_no_breakout_within_prior_range():
    highs = [D(100)] * 21
    lows = [D(90)] * 21
    closes = [D(95)] * 21
    result = rolling_range(highs, lows, closes, 20)
    assert result["breakout"] == "NONE"


def test_breakout_excludes_todays_own_bar_from_the_reference_range():
    # If today were counted in its own reference range, a huge up-move would
    # inflate the "prior high" to include itself and could never register as
    # a breakout -- every day would trivially be inside its own range.
    highs = [D(100)] * 20 + [D(200)]
    lows = [D(90)] * 20 + [D(90)]
    closes = [D(95)] * 20 + [D(150)]
    result = rolling_range(highs, lows, closes, 20)
    assert result["prior_high"] == D(100)   # not 200
    assert result["breakout"] == "BREAKOUT_UP"


# ---------------------------------------------------------------------------
# Database-level: point-in-time correctness and storage
# ---------------------------------------------------------------------------

class _FakeSource:
    source_id = "TEST_PRICES"
    key_scheme = "US_TICKER"


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
        cur.execute("INSERT INTO instruments (isin) VALUES ('INE000TECH01') "
                     "RETURNING instrument_id")
        instrument_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO instrument_external_ids (instrument_id, scheme, value, source_id) "
            "VALUES (%s, 'US_TICKER', 'TEST', 'TEST_PRICES')",
            (instrument_id,),
        )
    return instrument_id


def _write_trend(conn, instrument_id: int, start: date, n_days: int,
                  start_close: Decimal, daily_step: Decimal) -> None:
    bars, close, day = [], start_close, start
    for _ in range(n_days):
        bars.append(PriceBar(instrument_ref="TEST", day=day, open=close,
                              high=close + 1, low=close - 1, close=close, volume=1000))
        close += daily_step
        day = date.fromordinal(day.toordinal() + 1)
    write_bars(conn, _FakeSource(), "TEST", bars)


def test_compute_returns_none_with_no_price_history(conn):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO instruments (isin) VALUES ('INE000EMPTY2') "
                     "RETURNING instrument_id")
        empty_id = cur.fetchone()[0]
    assert compute(conn, empty_id, date(2026, 1, 1)) is None


def test_compute_ignores_bars_after_as_of(conn, priced_instrument):
    """The same guarantee factor_score.py's momentum test pins, checked here
    for the indicator engine: a bar dated after as_of must not change what
    was computed as of that date."""
    _write_trend(conn, priced_instrument, date(2024, 1, 1), LOOKBACK_SESSIONS,
                 start_close=D(100), daily_step=D("0.1"))
    as_of = date.fromordinal(date(2024, 1, 1).toordinal() + LOOKBACK_SESSIONS - 1)
    before = compute(conn, priced_instrument, as_of)

    future_day = date.fromordinal(as_of.toordinal() + 1)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO price_bars (instrument_id, day, open, high, low, close, "
            "volume, source_id) VALUES (%s, %s, 9000, 9500, 8900, 9400, 1000, 'TEST_PRICES')",
            (priced_instrument, future_day),
        )
    after = compute(conn, priced_instrument, as_of)
    assert after == before


def test_compute_and_store_is_an_upsert_not_append_only(conn, priced_instrument):
    """Unlike price_bars, technical_indicators is derived and must be
    updatable: recomputing the same as_of after new data arrives updates the
    row in place rather than accumulating stale duplicates."""
    _write_trend(conn, priced_instrument, date(2024, 1, 1), LOOKBACK_SESSIONS,
                 start_close=D(100), daily_step=D("0.1"))
    as_of = date.fromordinal(date(2024, 1, 1).toordinal() + LOOKBACK_SESSIONS - 1)

    written_first = compute_and_store(conn, [priced_instrument], as_of)
    assert written_first == 1
    written_second = compute_and_store(conn, [priced_instrument], as_of)
    assert written_second == 1

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM technical_indicators WHERE instrument_id = %s",
                     (priced_instrument,))
        assert cur.fetchone()[0] == 1   # one row, not two
