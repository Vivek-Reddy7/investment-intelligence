"""The sizing calculator: pure arithmetic, no selection logic to test
because there isn't any -- these tests exist to pin exactly that boundary."""

from __future__ import annotations

from decimal import Decimal

import pytest

from investment_intelligence.analytics.sizing import (
    Holding,
    MixedCurrency,
    size_equal_weight,
)


def D(x) -> Decimal:
    return Decimal(str(x))


def test_equal_split_across_two_holdings():
    holdings = [
        Holding(1, "AAA", D(100), "USD"),
        Holding(2, "BBB", D(50), "USD"),
    ]
    result = size_equal_weight(D(1000), "USD", holdings)
    assert result.per_instrument_budget == D(500)
    by_ticker = {a.ticker: a for a in result.allocations}
    assert by_ticker["AAA"].shares == 5     # 500 // 100
    assert by_ticker["BBB"].shares == 10    # 500 // 50
    assert result.total_spent == D(1000)
    assert result.leftover == D(0)


def test_leftover_when_price_does_not_divide_evenly():
    holdings = [Holding(1, "AAA", D(30), "USD")]
    result = size_equal_weight(D(100), "USD", holdings)
    assert result.allocations[0].shares == 3   # 100 // 30
    assert result.allocations[0].cost == D(90)
    assert result.leftover == D(10)


def test_zero_shares_when_the_budget_cannot_afford_one_share():
    # A ₹500 budget split across an expensive holding: the honest answer is
    # zero shares and the full amount left over, not a fractional share or
    # an error -- this is the case the original UX example (₹500) actually
    # needs to handle correctly.
    holdings = [Holding(1, "PRICEY", D(10000), "USD")]
    result = size_equal_weight(D(500), "USD", holdings)
    assert result.allocations[0].shares == 0
    assert result.leftover == D(500)


def test_mixed_currency_is_refused_not_silently_wrong():
    holdings = [
        Holding(1, "AAA", D(100), "USD"),
        Holding(2, "BBB", D(8000), "INR"),
    ]
    with pytest.raises(MixedCurrency):
        size_equal_weight(D(1000), "USD", holdings)


def test_empty_holdings_is_refused():
    with pytest.raises(ValueError):
        size_equal_weight(D(1000), "USD", [])


def test_split_is_even_regardless_of_holding_order():
    # The budget split must depend only on the COUNT of holdings, not which
    # one happens to be listed first -- a real bug shape if the budget were
    # ever accidentally computed from just the first holding's price.
    holdings_a = [Holding(1, "AAA", D(100), "USD"), Holding(2, "BBB", D(9999), "USD")]
    holdings_b = [Holding(2, "BBB", D(9999), "USD"), Holding(1, "AAA", D(100), "USD")]
    assert (size_equal_weight(D(1000), "USD", holdings_a).per_instrument_budget ==
            size_equal_weight(D(1000), "USD", holdings_b).per_instrument_budget ==
            D(500))
