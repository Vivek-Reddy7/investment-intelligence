"""Combines fundamental and technical factor ranks into one explainable score.

Your requirement #5, built directly on top of today's two separate pieces
rather than a new engine: factor_score.py already ranks momentum, quality
and revenue growth; technicals.py already computes SMA/RSI/MACD/Bollinger.
This module adds two NEW technical factors to the SAME rank-combination the
fundamental score already uses, and stores the result in the SAME
factor_scores table under a different model_version -- the table was built
model-version-aware from the start for exactly this.

Two technical factors were added, not five, and the choice of which two is
the actual design decision here, worth stating rather than defaulting
silently:

  trend_strength   (close - SMA_200) / SMA_200 -- continuous and monotonic:
                    further above its own 200-day average is a stronger
                    uptrend, further below is a stronger downtrend. Ranks
                    the same direction as momentum, quality and growth do:
                    higher is "better" in the same sense throughout.

  macd_momentum    the MACD histogram -- positive and rising means bullish
                    momentum is strengthening. Also monotonic in the same
                    direction.

RSI is deliberately EXCLUDED from the composite, even though it is computed
and shown separately in the local report. RSI is not monotonic in the way
this composite needs: a high RSI can mean strong momentum (bullish) or an
overbought condition due for a reversal (bearish), depending on which
technical-analysis school you ask, and blending it into an equal-weighted
"higher rank is better" composite would silently encode one of those two
readings as correct. That is exactly the kind of undisclosed modelling
choice this project's own compliance test exists to keep out: a calculation
should not quietly contain an opinion about which interpretation is right.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import psycopg

from investment_intelligence.analytics import factor_score, technicals

MODEL_VERSION = "combined-v1-rank"


def _trend_strength(conn: psycopg.Connection, instrument_id: int, as_of: date) -> Decimal | None:
    rows = technicals.price_series_asof(conn, instrument_id, as_of)
    if not rows:
        return None
    closes = [r[3] for r in rows]
    s200 = technicals.sma(closes, 200)
    if s200 is None or s200 == 0:
        return None
    return (closes[-1] - s200) / s200


def _macd_momentum(conn: psycopg.Connection, instrument_id: int, as_of: date) -> Decimal | None:
    rows = technicals.price_series_asof(conn, instrument_id, as_of)
    if not rows:
        return None
    closes = [r[3] for r in rows]
    _, _, hist = technicals.macd(closes)
    return hist


def compute_scores(
    conn: psycopg.Connection, instrument_ids: list[int], as_of: date
) -> list[factor_score.FactorScore]:
    """Five named factors, equal-weighted percentile rank, same discipline
    as factor_score.compute_scores: an instrument missing a factor is
    excluded from THAT factor's ranking rather than defaulted, and a score
    is dropped entirely if it has zero factors available -- never stored as
    a fabricated bottom rank."""
    fundamental_inputs = factor_score.compute_inputs(conn, instrument_ids, as_of)

    trend = {i.instrument_id: _trend_strength(conn, i.instrument_id, as_of)
             for i in fundamental_inputs}
    macd_m = {i.instrument_id: _macd_momentum(conn, i.instrument_id, as_of)
              for i in fundamental_inputs}

    momentum_ranks = factor_score._percentile_ranks(
        {i.instrument_id: i.momentum_return for i in fundamental_inputs
         if i.momentum_return is not None})
    quality_ranks = factor_score._percentile_ranks(
        {i.instrument_id: i.net_margin for i in fundamental_inputs
         if i.net_margin is not None})
    growth_ranks = factor_score._percentile_ranks(
        {i.instrument_id: i.revenue_growth for i in fundamental_inputs
         if i.revenue_growth is not None})
    trend_ranks = factor_score._percentile_ranks(
        {iid: v for iid, v in trend.items() if v is not None})
    macd_ranks = factor_score._percentile_ranks(
        {iid: v for iid, v in macd_m.items() if v is not None})

    scores = []
    for item in fundamental_inputs:
        iid = item.instrument_id
        components = {}
        if iid in momentum_ranks:
            components["momentum"] = {"value": str(item.momentum_return),
                                       "rank": momentum_ranks[iid]}
        if iid in quality_ranks:
            components["quality_net_margin"] = {"value": str(item.net_margin),
                                                  "rank": quality_ranks[iid]}
        if iid in growth_ranks:
            components["growth_revenue"] = {"value": str(item.revenue_growth),
                                             "rank": growth_ranks[iid]}
        if iid in trend_ranks:
            components["trend_strength"] = {"value": str(trend[iid]),
                                             "rank": trend_ranks[iid]}
        if iid in macd_ranks:
            components["macd_momentum"] = {"value": str(macd_m[iid]),
                                            "rank": macd_ranks[iid]}
        if not components:
            continue

        composite = sum(Decimal(str(c["rank"])) for c in components.values()) / len(components)
        scores.append(factor_score.FactorScore(
            instrument_id=iid,
            composite_score=composite,
            factors={"as_of": as_of.isoformat(), "components": components,
                     "factors_available": len(components), "factors_total": 5},
        ))
    return scores
