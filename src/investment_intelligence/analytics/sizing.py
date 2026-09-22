"""Turns an amount and a list of instruments into share counts. Arithmetic
only -- this module makes no selection decision itself.

This is the direct answer to the opening ask: "enter the amount you're
willing to invest... the system should identify relevant opportunities."
The selection half of that sentence is the combined score (or the
fundamental screener, or the factor score) -- this module is only the second
half, sizing an allocation across a list the CALLER already decided on. It
never ranks, filters, or chooses an instrument; it takes a list and answers
"how many shares of each, and how much is left over."

Equal weight, not because it is optimal -- for most purposes it is not --
but because any other weighting (by market cap, by conviction, by risk
contribution) is itself a decision about which names matter more than
others, and making that decision inside a module whose whole point is to be
opinion-free would smuggle a recommendation into what is supposed to be a
calculator. Equal weight is the one scheme that adds no view.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class MixedCurrency(ValueError):
    """Every holding must share one currency. Splitting an INR amount across
    a USD price and calling the result a share count would silently produce
    a wrong number rather than an error -- refusing is the only safe move
    when there is no exchange rate in the room to convert with."""


@dataclass(frozen=True)
class Holding:
    instrument_id: int
    ticker: str
    price: Decimal
    currency: str


@dataclass(frozen=True)
class Allocation:
    instrument_id: int
    ticker: str
    price: Decimal
    shares: int
    cost: Decimal


@dataclass(frozen=True)
class SizingResult:
    currency: str
    per_instrument_budget: Decimal
    allocations: list[Allocation]
    total_spent: Decimal
    leftover: Decimal


def size_equal_weight(amount: Decimal, currency: str, holdings: list[Holding]) -> SizingResult:
    if not holdings:
        raise ValueError("no holdings to size across")
    mismatched = {h.ticker for h in holdings if h.currency != currency}
    if mismatched:
        raise MixedCurrency(
            f"amount is {currency} but these holdings are priced in a different "
            f"currency, and there is no exchange rate here to convert with: "
            f"{sorted(mismatched)}"
        )

    per_instrument_budget = amount / len(holdings)
    allocations = []
    total_spent = Decimal(0)
    for h in holdings:
        # Whole shares only -- this project does not model fractional-share
        # brokerage, which not every broker offers and which changes what
        # "how many shares" even means.
        shares = int(per_instrument_budget // h.price)
        cost = shares * h.price
        allocations.append(Allocation(instrument_id=h.instrument_id, ticker=h.ticker,
                                       price=h.price, shares=shares, cost=cost))
        total_spent += cost

    return SizingResult(
        currency=currency,
        per_instrument_budget=per_instrument_budget,
        allocations=allocations,
        total_spent=total_spent,
        leftover=amount - total_spent,
    )
