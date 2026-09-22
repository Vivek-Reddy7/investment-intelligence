"""Price bars: boundary validation, idempotent writes, append-only enforcement.

Not tested against live Yahoo Finance -- same reasoning as test_edgar.py:
hammering a rate-limited third party in a suite is rude and unreliable, and
the interesting assertions are about our own validation and write logic, not
about the network.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import psycopg
import pytest

from investment_intelligence.ingest.price_writer import write_bars
from investment_intelligence.ingest.writer import UnknownInstrument
from investment_intelligence.sources.base import Rejection
from investment_intelligence.sources.prices import InvalidBar, PriceBar


class _FakeSource:
    """A PriceSource that returns pre-built bars instead of calling yfinance."""
    source_id = "TEST_PRICES"
    key_scheme = "US_TICKER"


def _bar(day: date, o="100", h="105", l="99", c="104", v=1_000_000) -> PriceBar:
    return PriceBar(instrument_ref="TEST", day=day, open=Decimal(o), high=Decimal(h),
                     low=Decimal(l), close=Decimal(c), volume=v)


# ---------------------------------------------------------------------------
# Boundary validation -- a malformed bar cannot exist as an object
# ---------------------------------------------------------------------------

def test_rejects_high_below_low():
    with pytest.raises(InvalidBar, match="high .* < low"):
        _bar(date(2026, 1, 2), h="90", l="99")


def test_rejects_open_outside_range():
    with pytest.raises(InvalidBar, match="outside"):
        _bar(date(2026, 1, 2), o="200")


def test_rejects_close_outside_range():
    with pytest.raises(InvalidBar, match="outside"):
        _bar(date(2026, 1, 2), c="1")


def test_rejects_negative_volume():
    with pytest.raises(InvalidBar, match="negative volume"):
        _bar(date(2026, 1, 2), v=-1)


def test_accepts_a_well_formed_bar():
    bar = _bar(date(2026, 1, 2))
    assert bar.close == Decimal("104")


# ---------------------------------------------------------------------------
# Writing -- idempotent, and an unknown instrument is a rejection not a crash
# ---------------------------------------------------------------------------

@pytest.fixture
def price_source_row(conn):
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
    return "TEST_PRICES"


@pytest.fixture
def instrument(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO instruments (isin) VALUES ('INE000PRICE1') "
                     "RETURNING instrument_id")
        instrument_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO instrument_external_ids (instrument_id, scheme, value, source_id) "
            "VALUES (%s, 'US_TICKER', 'TEST', 'TEST_PRICES')",
            (instrument_id,),
        )
    return instrument_id


def test_write_bars_is_idempotent(conn, price_source_row, instrument):
    bars = [_bar(date(2026, 1, 2)), _bar(date(2026, 1, 5), o="104", h="110", l="103", c="108")]

    first = write_bars(conn, _FakeSource(), "TEST", bars)
    assert first.bars_written == 2
    assert first.bars_unchanged == 0

    # Re-fetching the SAME window -- what a re-run of `prices` does -- must
    # not create a second row per day.
    second = write_bars(conn, _FakeSource(), "TEST", bars)
    assert second.bars_written == 0
    assert second.bars_unchanged == 2

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM price_bars WHERE instrument_id = %s", (instrument,))
        assert cur.fetchone()[0] == 2


def test_write_bars_unknown_instrument_is_a_rejection_not_a_crash(conn, price_source_row):
    result = write_bars(conn, _FakeSource(), "NO-SUCH-TICKER", [_bar(date(2026, 1, 2))])
    assert result.bars_written == 0
    assert len(result.rejections) == 1
    assert result.rejections[0].reason == "UNKNOWN_INSTRUMENT"


def test_write_bars_passes_through_source_rejections(conn, price_source_row, instrument):
    items = [_bar(date(2026, 1, 2)),
             Rejection(instrument_ref="TEST", detail="NaN row", reason="INCOMPLETE_BAR")]
    result = write_bars(conn, _FakeSource(), "TEST", items)
    assert result.bars_written == 1
    assert len(result.rejections) == 1
    assert result.rejections[0].reason == "INCOMPLETE_BAR"


# ---------------------------------------------------------------------------
# Append-only -- the trigger from migration 024, exercised for real
# ---------------------------------------------------------------------------

def test_price_bars_rejects_update(conn, price_source_row, instrument):
    write_bars(conn, _FakeSource(), "TEST", [_bar(date(2026, 1, 2))])
    with conn.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            cur.execute("UPDATE price_bars SET close = 999 "
                        "WHERE instrument_id = %s AND day = '2026-01-02'", (instrument,))
    conn.rollback()


def test_price_bars_rejects_delete(conn, price_source_row, instrument):
    write_bars(conn, _FakeSource(), "TEST", [_bar(date(2026, 1, 2))])
    with conn.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            cur.execute("DELETE FROM price_bars WHERE instrument_id = %s", (instrument,))
    conn.rollback()
