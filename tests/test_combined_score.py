"""The combined fundamental + technical score.

Most of the arithmetic (percentile ranking, SMA, MACD) already has its own
tests in test_factor_score.py and test_technicals.py -- these tests are
about what this module adds on top: which two technical factors get
blended in, that RSI genuinely is not one of them (not just documented as
excluded), and that it degrades gracefully when technical data is missing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from conftest import add_fact, add_filing, add_lifecycle, utc
from investment_intelligence.analytics.combined_score import compute_scores
from investment_intelligence.ingest.price_writer import write_bars
from investment_intelligence.sources.prices import PriceBar

FY = 2025


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


def _company_with_fundamentals(conn, source, isin: str) -> int:
    """Two fiscal years, not one -- REVENUE_GROWTH is year-over-year, and
    metrics_as_of needs a prior-year revenue row to compute it against."""
    with conn.cursor() as cur:
        cur.execute("INSERT INTO instruments (isin) VALUES (%s) RETURNING instrument_id",
                     (isin,))
        instrument_id = cur.fetchone()[0]
        cur.execute("INSERT INTO tracked_instruments (instrument_id) VALUES (%s)",
                     (instrument_id,))
    add_lifecycle(conn, instrument_id, source, event="LISTED",
                  event_date=date(2015, 1, 1), known_from=utc(2015, 1, 1))

    for fy, revenue in ((FY - 1, 800), (FY, 1000)):
        known = utc(fy, 6, 1)
        filing = add_filing(conn, instrument_id, source, period_end=date(fy, 3, 31),
                            filed_at=known, ref=f"{isin}-fy{fy}")
        values = {"revenue": revenue, "net_profit": revenue // 10,
                  "total_assets": 1000, "total_equity": 500}
        for item, value in values.items():
            add_fact(conn, instrument_id, source, filing, line_item=item.upper(),
                     value=Decimal(str(value)), fiscal_year=fy, period_type="ANNUAL",
                     known_from=known, period_start=date(fy - 1, 4, 1), period_end=date(fy, 3, 31))
    return instrument_id


def _add_prices(conn, instrument_id: int, ticker: str, start: date, n_days: int,
                start_close: Decimal, daily_step: Decimal) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO instrument_external_ids (instrument_id, scheme, value, source_id) "
            "VALUES (%s, 'US_TICKER', %s, 'TEST_PRICES') ON CONFLICT DO NOTHING",
            (instrument_id, ticker),
        )
    bars, close, day = [], start_close, start
    for _ in range(n_days):
        bars.append(PriceBar(instrument_ref=ticker, day=day, open=close, high=close + 1,
                              low=close - 1, close=close, volume=1000))
        close += daily_step
        day = date.fromordinal(day.toordinal() + 1)
    write_bars(conn, _FakeSource(), ticker, bars)


def test_rsi_is_not_among_the_blended_components(conn, source, price_source):
    """The documented exclusion, checked in code: RSI must never appear as
    a named component of the combined score, however many other factors
    are available."""
    iid = _company_with_fundamentals(conn, source, "INE000COMB01")
    _add_prices(conn, iid, "COMB1", date(2024, 1, 1), 260, Decimal(100), Decimal("0.1"))

    scores = compute_scores(conn, [iid], date.fromordinal(date(2024, 1, 1).toordinal() + 259))
    assert len(scores) == 1
    names = set(scores[0].factors["components"])
    assert "rsi_14" not in names
    assert "rsi" not in names


def test_blends_trend_and_macd_when_price_history_is_sufficient(conn, source, price_source):
    iid = _company_with_fundamentals(conn, source, "INE000COMB02")
    # as_of must be on or after FY2025's filing (2025-06-01) for growth_revenue
    # to be knowable at all, and needs >= LOOKBACK_SESSIONS of PRIOR price
    # history for the technical factors -- 700 days from 2023-06-01 covers both.
    _add_prices(conn, iid, "COMB2", date(2023, 6, 1), 700, Decimal(100), Decimal("0.1"))

    scores = compute_scores(conn, [iid], date(2025, 7, 1))
    assert len(scores) == 1
    names = set(scores[0].factors["components"])
    assert {"momentum", "quality_net_margin", "growth_revenue",
            "trend_strength", "macd_momentum"} <= names
    assert scores[0].factors["factors_total"] == 5
    assert scores[0].factors["factors_available"] == 5


def test_degrades_gracefully_with_no_price_history(conn, source, price_source):
    """A company with fundamentals but zero price bars must still produce a
    score, built from the fundamental factors that do not need price data --
    not be dropped entirely just because the technical half, and price-
    derived momentum with it, is unavailable."""
    iid = _company_with_fundamentals(conn, source, "INE000COMB03")

    scores = compute_scores(conn, [iid], date(FY, 12, 31))
    assert len(scores) == 1
    names = set(scores[0].factors["components"])
    # momentum is ALSO price-derived (factor_score.py), so with zero price
    # history only the two purely-fundamental factors can be present.
    assert names == {"quality_net_margin", "growth_revenue"}
    assert scores[0].factors["factors_available"] == 2


def test_drops_instrument_with_zero_factors_of_any_kind(conn, price_source):
    """No fundamentals filed and no price history at all: the instrument
    must not appear in the output -- a score with zero real inputs would be
    indistinguishable from a genuine bottom-percentile result."""
    with conn.cursor() as cur:
        cur.execute("INSERT INTO instruments (isin) VALUES ('INE000COMB04') "
                     "RETURNING instrument_id")
        empty_id = cur.fetchone()[0]

    scores = compute_scores(conn, [empty_id], date(2026, 1, 1))
    assert scores == []
