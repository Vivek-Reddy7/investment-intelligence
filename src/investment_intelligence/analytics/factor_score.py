"""A factor score: momentum, quality and growth, combined by rank.

Why a rank-combination and not a trained model. The tracked universe is nine
companies with roughly a decade of annual-mostly fundamentals -- on the order
of 100-150 usable (instrument, fiscal_year) rows. That is not a machine
learning problem; it is a small-sample statistics problem wearing an ML
costume. A gradient-boosted model or a neural net fit on 150 rows will find
a pattern, report it confidently, and that pattern will be noise -- the
model has more capacity than the data has information. An equal-weighted
percentile rank across a small number of named, disclosed factors is the
honest tool at this sample size: it cannot overfit, because it does not fit
anything. Every input is visible, and the score is a description of where an
instrument sits among its tracked peers today, not a forecast trained to
look confident.

This is also the same compliance test the rest of the project already
applies (docs/01-vision-and-scope.md): "can it be stated as a calculation
rather than an opinion?" A percentile rank on named factors is a calculation.
A trained model's output, presented as Buy/Sell with a confidence score,
reads as an opinion regardless of what produced it. The math here stays on
the calculation side of that line on purpose.

Point-in-time discipline: every factor is computed using only information
dated on or before `as_of`. Momentum reads price_bars up to and including
`as_of`. Quality and growth read `metrics_as_of(as_of)`, the same function
the historical screener already uses -- this module adds no new lookahead
risk, it reuses the guarantee that already exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import psycopg

MODEL_VERSION = "factor-v1-rank"

# Trailing window for momentum. ~126 trading days is approximately six
# calendar months; picked because six-month momentum is the horizon most
# consistently used in factor-investing literature, not tuned against this
# data -- tuning a lookback window on nine companies would itself be a
# small-sample overfit of the kind this module exists to avoid.
MOMENTUM_TRADING_DAYS = 126


@dataclass(frozen=True)
class FactorInputs:
    instrument_id: int
    momentum_return: Decimal | None
    net_margin: Decimal | None
    revenue_growth: Decimal | None


def _momentum(conn: psycopg.Connection, instrument_id: int, as_of: date) -> Decimal | None:
    """Trailing return: close at as_of vs. close MOMENTUM_TRADING_DAYS
    trading days earlier. None if there is not enough history -- an absent
    factor must not silently become a zero, which would rank a data gap as
    "flat" rather than as missing."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT day, close FROM price_bars
            WHERE instrument_id = %s AND day <= %s
            ORDER BY day DESC
            LIMIT %s
            """,
            (instrument_id, as_of, MOMENTUM_TRADING_DAYS + 1),
        )
        rows = cur.fetchall()
    if len(rows) <= MOMENTUM_TRADING_DAYS:
        return None
    latest_close = rows[0][1]
    earliest_close = rows[-1][1]
    if earliest_close == 0:
        return None
    return (latest_close - earliest_close) / earliest_close


def _latest_metrics(
    conn: psycopg.Connection, instrument_id: int, as_of: date
) -> dict[str, Decimal]:
    """The most recently knowable NET_MARGIN and REVENUE_GROWTH for one
    instrument as of `as_of`, via metrics_as_of -- the same point-in-time
    function the historical screener already relies on. "Most recent" means
    the latest fiscal_year among rows that were knowable by `as_of`, which is
    what DISTINCT ON with this ordering selects.

    Pinned to period_type = 'ANNUAL'. metrics_as_of can return the same
    metric_code more than once per fiscal_year -- once per period_type/basis/
    currency combination -- and without pinning one, DISTINCT ON would pick
    whichever row postgres happens to scan first, which is silent
    nondeterminism in a number that ends up in a stored score."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (metric_code) metric_code, value
            FROM metrics_as_of(%s::timestamptz)
            WHERE instrument_id = %s AND period_type = 'ANNUAL'
              AND metric_code IN ('NET_MARGIN', 'REVENUE_GROWTH')
            ORDER BY metric_code, fiscal_year DESC
            """,
            (as_of, instrument_id),
        )
        return {code: value for code, value in cur.fetchall()}


def compute_inputs(
    conn: psycopg.Connection, instrument_ids: list[int], as_of: date
) -> list[FactorInputs]:
    results = []
    for iid in instrument_ids:
        metrics = _latest_metrics(conn, iid, as_of)
        results.append(FactorInputs(
            instrument_id=iid,
            momentum_return=_momentum(conn, iid, as_of),
            net_margin=metrics.get("NET_MARGIN"),
            revenue_growth=metrics.get("REVENUE_GROWTH"),
        ))
    return results


def _percentile_ranks(values: dict[int, Decimal]) -> dict[int, float]:
    """0-1 rank within the given instruments, higher value -> higher rank.
    Instruments missing the factor are excluded from this factor's ranking
    entirely rather than defaulted to a rank -- a company with no momentum
    history should not silently score as "median momentum"."""
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda kv: kv[1])
    n = len(ordered)
    if n == 1:
        return {ordered[0][0]: 0.5}
    return {iid: i / (n - 1) for i, (iid, _) in enumerate(ordered)}


@dataclass(frozen=True)
class FactorScore:
    instrument_id: int
    composite_score: Decimal
    factors: dict


def compute_scores(
    conn: psycopg.Connection, instrument_ids: list[int], as_of: date
) -> list[FactorScore]:
    """The composite: equal-weighted average of each factor's percentile
    rank, computed only over instruments that HAVE that factor. An
    instrument missing every factor is dropped rather than scored -- a
    score with zero real inputs would be indistinguishable from a genuine
    bottom-percentile score, which is the wrong kind of silent."""
    inputs = compute_inputs(conn, instrument_ids, as_of)

    momentum_ranks = _percentile_ranks(
        {i.instrument_id: i.momentum_return for i in inputs if i.momentum_return is not None})
    quality_ranks = _percentile_ranks(
        {i.instrument_id: i.net_margin for i in inputs if i.net_margin is not None})
    growth_ranks = _percentile_ranks(
        {i.instrument_id: i.revenue_growth for i in inputs if i.revenue_growth is not None})

    scores = []
    for item in inputs:
        iid = item.instrument_id
        components = {}
        if iid in momentum_ranks:
            components["momentum"] = {
                "value": str(item.momentum_return), "rank": momentum_ranks[iid],
                "window_trading_days": MOMENTUM_TRADING_DAYS,
            }
        if iid in quality_ranks:
            components["quality_net_margin"] = {
                "value": str(item.net_margin), "rank": quality_ranks[iid],
            }
        if iid in growth_ranks:
            components["growth_revenue"] = {
                "value": str(item.revenue_growth), "rank": growth_ranks[iid],
            }
        if not components:
            continue

        composite = sum(Decimal(str(c["rank"])) for c in components.values()) / len(components)
        scores.append(FactorScore(
            instrument_id=iid,
            composite_score=composite,
            factors={"as_of": as_of.isoformat(), "components": components,
                     "factors_available": len(components)},
        ))
    return scores


def store_scores(conn: psycopg.Connection, as_of: date, scores: list[FactorScore],
                 model_version: str = MODEL_VERSION) -> int:
    """`model_version` defaults to THIS module's own, but must be passed
    explicitly by any other caller -- combined_score.py reuses this same
    writer for its five-factor scores, and the default silently writing
    everything under "factor-v1-rank" was a real bug: it overwrote the
    fundamental-only scores with the combined ones under the wrong label,
    found by a sizing query that came back empty and traced back to why."""
    import json
    written = 0
    with conn.cursor() as cur:
        for s in scores:
            cur.execute(
                """
                INSERT INTO factor_scores
                    (instrument_id, as_of, model_version, composite_score, factors)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (instrument_id, as_of, model_version) DO UPDATE
                   SET composite_score = excluded.composite_score,
                       factors = excluded.factors,
                       computed_at = now()
                """,
                (s.instrument_id, as_of, model_version, s.composite_score,
                 json.dumps(s.factors)),
            )
            written += 1
    return written
