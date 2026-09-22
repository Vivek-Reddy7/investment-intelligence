"""Market cap: the point-in-time share-count selection (pure function, a
synthetic payload rather than the live API), and the full compute-and-store
path against real price_bars."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from investment_intelligence.analytics.market_cap import (
    SharesOutstanding,
    _pick_shares_asof,
    _shares_entries,
    compute_and_store,
)
from investment_intelligence.ingest.price_writer import write_bars
from investment_intelligence.sources.prices import PriceBar

# A realistic slice of what EDGAR's companyfacts dei.EntityCommonStockShares-
# Outstanding.units.shares actually returns (verified against the live API
# for INFY before writing this).
ENTRIES = [
    {"end": "2024-03-31", "val": 4150867464, "filed": "2024-06-24"},
    {"end": "2025-03-31", "val": 4153263455, "filed": "2025-07-01"},
    {"end": "2026-03-31", "val": 4055591723, "filed": "2026-06-15"},
]


def test_picks_the_most_recently_filed_count_on_or_before_as_of():
    result = _pick_shares_asof(ENTRIES, date(2025, 12, 1))
    assert result.value == 4153263455   # the 2025-07-01 filing, not the 2026 one
    assert result.filed == date(2025, 7, 1)


def test_excludes_a_count_filed_after_as_of_even_though_its_end_date_is_earlier():
    # The 2026-06-15 filing describes 2026-03-31, but as_of here is BEFORE
    # that filing date -- it was not yet public. This is the actual
    # point-in-time guarantee, and it is the case a naive "pick the row
    # with the latest `end` <= as_of" implementation would get wrong.
    result = _pick_shares_asof(ENTRIES, date(2026, 1, 1))
    assert result.filed == date(2025, 7, 1)
    assert result.value == 4153263455


def test_none_when_nothing_was_filed_yet():
    assert _pick_shares_asof(ENTRIES, date(2020, 1, 1)) is None


def test_none_with_empty_entries():
    assert _pick_shares_asof([], date(2026, 1, 1)) is None


# ---------------------------------------------------------------------------
# _shares_entries -- dei preferred, ifrs-full fallback, neither is a fabrication
# ---------------------------------------------------------------------------

def test_prefers_dei_when_both_taxonomies_are_present():
    facts = {
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [{"val": 1}]}}},
        "ifrs-full": {"NumberOfSharesOutstanding": {"units": {"shares": [{"val": 2}]}}},
    }
    assert _shares_entries(facts) == [{"val": 1}]


def test_falls_back_to_ifrs_full_when_dei_is_absent():
    # The real shape for a filer like YTRA: no dei entry for this concept at
    # all, the same data reported under a different taxonomy instead.
    facts = {"ifrs-full": {"NumberOfSharesOutstanding": {"units": {"shares": [{"val": 2}]}}}}
    assert _shares_entries(facts) == [{"val": 2}]


def test_empty_when_neither_taxonomy_has_it():
    # The real shape for a filer like MMYT: not a bug, a genuine absence --
    # see docs/backlog.md. No weighted-average-shares substitute here.
    facts = {"ifrs-full": {"AdjustedWeightedAverageShares": {"units": {"shares": [{"val": 3}]}}}}
    assert _shares_entries(facts) == []


# ---------------------------------------------------------------------------
# compute_and_store -- against real price_bars, with a fake EDGAR fetch
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
        cur.execute("INSERT INTO instruments (isin) VALUES ('INE000MCAP01') "
                     "RETURNING instrument_id")
        instrument_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO instrument_external_ids (instrument_id, scheme, value, source_id) "
            "VALUES (%s, 'US_TICKER', 'TEST', 'TEST_PRICES')",
            (instrument_id,),
        )
    write_bars(conn, _FakeSource(), "TEST",
              [PriceBar(instrument_ref="TEST", day=date(2026, 1, 2), open=Decimal(10),
                        high=Decimal(11), low=Decimal(9), close=Decimal(10), volume=1000)])
    return instrument_id


def test_compute_and_store_multiplies_shares_by_price(conn, priced_instrument, monkeypatch):
    monkeypatch.setattr(
        "investment_intelligence.analytics.market_cap.fetch_shares_outstanding",
        lambda cik, as_of: SharesOutstanding(
            value=1000, shares_as_of=date(2025, 12, 31), filed=date(2026, 1, 1)),
    )
    market_cap = compute_and_store(conn, priced_instrument, cik=1067491, as_of=date(2026, 1, 2))
    assert market_cap == Decimal(1000) * Decimal(10)   # shares * close

    with conn.cursor() as cur:
        cur.execute("SELECT shares_outstanding, price, currency, market_cap "
                     "FROM market_cap_snapshots WHERE instrument_id = %s", (priced_instrument,))
        row = cur.fetchone()
    assert row == (1000, Decimal(10), "USD", Decimal(10000))


def test_compute_and_store_none_when_no_price_history(conn, monkeypatch):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO instruments (isin) VALUES ('INE000MCAP02') "
                     "RETURNING instrument_id")
        empty_id = cur.fetchone()[0]

    monkeypatch.setattr(
        "investment_intelligence.analytics.market_cap.fetch_shares_outstanding",
        lambda cik, as_of: SharesOutstanding(
            value=1000, shares_as_of=date(2025, 12, 31), filed=date(2026, 1, 1)),
    )
    assert compute_and_store(conn, empty_id, cik=999, as_of=date(2026, 1, 2)) is None


def test_compute_and_store_none_when_shares_outstanding_is_unavailable(
    conn, priced_instrument, monkeypatch
):
    # EDGAR has no shares-outstanding figure for this filer as of this date
    # (the real MMYT case, or a filer with no dei/ifrs-full entry yet) --
    # must stop here rather than fall through to a price lookup for a
    # market cap it cannot compute.
    monkeypatch.setattr(
        "investment_intelligence.analytics.market_cap.fetch_shares_outstanding",
        lambda cik, as_of: None,
    )
    assert compute_and_store(conn, priced_instrument, cik=1067491, as_of=date(2026, 1, 2)) is None

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM market_cap_snapshots WHERE instrument_id = %s",
                     (priced_instrument,))
        assert cur.fetchone()[0] == 0
