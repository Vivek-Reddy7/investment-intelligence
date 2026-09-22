"""Risk metrics: annualised volatility, max drawdown, historical VaR.

A different question from technicals.py, computed from the same prices.
Technicals answer "what is the price doing" -- trend, momentum, overbought/
oversold. These answer "how much has this moved against someone holding it,
historically" -- and that distinction is the reason this is a separate
module and a separate table (migration 030), not three more entries in
technicals.py's indicators dict.

Every number here describes the PAST. None of it is a prediction, and
"volatility was high" is not "volatility will be high" -- the same
calculation-not-opinion boundary the rest of this project holds everywhere
else. A risk metric is exactly the kind of number that tempts a Buy/Sell
framing ("high VaR, so avoid") and this module deliberately stops at the
number.

Uses SIMPLE daily returns throughout, not log returns, for one reason:
consistency with the rest of this codebase, which already computes simple
returns everywhere else (factor_score.py's momentum, backtest.py's
forward_return). Log returns are the more common convention specifically
for volatility work, because they are additive across time and better
approximate a normal distribution -- a real trade-off, noted rather than
silently taken either way. At daily granularity and the return magnitudes
these instruments actually show, the numerical difference between the two
conventions is small; the consistency this codebase already established
was judged more valuable than chasing the textbook-standard choice for one
module in isolation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import psycopg

from investment_intelligence.analytics.technicals import price_series_asof

MODEL_VERSION = "risk-v1"

# ~1 trading year, matching technicals.py's LOOKBACK_SESSIONS -- long enough
# for volatility and drawdown to describe a meaningful stretch, short enough
# that a metric is not silently averaging over an instrument's entire
# multi-year history when someone asks about "now".
LOOKBACK_SESSIONS = 250

TRADING_DAYS_PER_YEAR = 252


def _daily_returns(closes: list[Decimal]) -> list[Decimal]:
    return [(closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(1, len(closes)) if closes[i - 1] != 0]


def annualized_volatility(closes: list[Decimal]) -> Decimal | None:
    """Sample standard deviation (ddof=1) of daily returns, scaled to a
    year by sqrt(252) -- the standard annualisation, assuming returns are
    roughly independent day to day. Needs at least 2 returns (3 closes) to
    have a defined sample variance at all."""
    returns = _daily_returns(closes)
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    daily_vol = variance.sqrt()
    return daily_vol * Decimal(TRADING_DAYS_PER_YEAR).sqrt()


def max_drawdown(closes: list[Decimal]) -> Decimal | None:
    """The worst peak-to-trough decline over the window, as a negative
    fraction -- -0.35 means a 35% fall from a prior high was seen somewhere
    in this window. Not "current distance from the all-time high": the
    worst drawdown may have already recovered by as_of, and this reports
    that it happened, not that it is still happening."""
    if len(closes) < 2:
        return None
    peak = closes[0]
    worst = Decimal(0)
    for price in closes:
        if price > peak:
            peak = price
        if peak > 0:
            drawdown = (price - peak) / peak
            if drawdown < worst:
                worst = drawdown
    return worst


def historical_var_95(closes: list[Decimal]) -> Decimal | None:
    """The 5th percentile of the daily-return distribution over the window
    -- "on the worst ~5% of days in this window, the loss was at least this
    large." Reported as a negative fraction. Historical, not model-based:
    no assumption that returns are normally distributed, which is also why
    it needs a real number of observations to mean anything -- refused
    below 20 returns rather than computed from too few to be anything but
    noise dressed as a statistic."""
    returns = _daily_returns(closes)
    if len(returns) < 20:
        return None
    ordered = sorted(returns)
    # Nearest-rank method: the 5th percentile index into a sorted ascending
    # list of n returns.
    index = int(len(ordered) * 0.05)
    return ordered[index]


def compute(conn: psycopg.Connection, instrument_id: int, as_of: date) -> dict | None:
    rows = price_series_asof(conn, instrument_id, as_of, n=LOOKBACK_SESSIONS)
    if not rows:
        return None
    closes = [r[3] for r in rows]

    out: dict = {"as_of": as_of.isoformat(), "sessions_available": len(rows)}

    vol = annualized_volatility(closes)
    if vol is not None:
        out["annualized_volatility"] = str(vol)

    dd = max_drawdown(closes)
    if dd is not None:
        out["max_drawdown"] = str(dd)

    var95 = historical_var_95(closes)
    if var95 is not None:
        out["historical_var_95"] = str(var95)

    if len(out) == 2:  # only as_of and sessions_available -- nothing computed
        return None
    return out


def compute_and_store(conn: psycopg.Connection, instrument_ids: list[int],
                      as_of: date) -> int:
    import json
    written = 0
    with conn.cursor() as cur:
        for iid in instrument_ids:
            metrics = compute(conn, iid, as_of)
            if metrics is None:
                continue
            cur.execute(
                """
                INSERT INTO risk_metrics (instrument_id, as_of, model_version, metrics)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (instrument_id, as_of, model_version) DO UPDATE
                   SET metrics = excluded.metrics, computed_at = now()
                """,
                (iid, as_of, MODEL_VERSION, json.dumps(metrics)),
            )
            written += 1
    return written
