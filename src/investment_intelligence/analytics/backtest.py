"""Backtesting: did the combined score actually have any predictive power?

Every score built today is a claim: "instruments ranked higher here are, in
some sense, better." Nothing built until now has checked whether that claim
is true. This module is that check, and it is built to be hard to fool
rather than easy to like -- the same instinct as everywhere else in this
project, applied to itself instead of to a data source.

Method: at each historical `as_of` date, recompute the combined score using
`combined_score.compute_scores` -- the EXACT SAME FUNCTION `make combined`
calls for today, unmodified, so there is no separate "backtest version" of
the scoring logic that could quietly differ from what actually runs live.
Point-in-time correctness is therefore inherited, not reimplemented: the
score at a past `as_of` genuinely could not see anything after it, because
that guarantee already lives in `compute_scores` and everything it calls.

The one deliberate exception, and it is a feature, not a leak: `forward_return`
looks at prices AFTER `as_of`. That is not the point-in-time rule being
broken -- it is the entire reason to run a backtest. You cannot grade a
score against what happened without eventually being allowed to know what
happened.

The headline number is the INFORMATION COEFFICIENT: the rank correlation
(Spearman) between composite_score and forward_return, pooled across every
(as_of, instrument) pair. Positive and meaningfully above zero means higher-
ranked names tended to do better afterward -- some real ordering power.
Near zero means the ranking contains no information a coin flip did not
already have. This is the standard way quant research grades a ranking
model, not invented for this project.

The honest caveat, stated once here rather than left implicit every time
the number is shown: nine-to-ten tracked companies over a handful of
non-overlapping historical windows is a genuinely small sample. An IC
computed from it is a real, checkable number, and it is also not powered
to distinguish real skill from noise the way a hedge fund's cross-section
of thousands of names would be. Report the number; do not oversell it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import psycopg

from investment_intelligence.analytics import combined_score

MODEL_VERSION = combined_score.MODEL_VERSION


@dataclass(frozen=True)
class BacktestPoint:
    instrument_id: int
    as_of: date
    composite_score: Decimal
    forward_days: int
    forward_return: Decimal


# How far the nearest available bar may sit from the exact target date and
# still count as "the forward_days price" -- covers weekends and the odd
# holiday cluster around it. A gap wider than this means the data simply
# does not extend far enough, and the honest answer is None, not a much
# shorter return silently relabelled with the requested horizon.
_FORWARD_LOOKUP_TOLERANCE_DAYS = 10


def _forward_return(conn: psycopg.Connection, instrument_id: int, as_of: date,
                    forward_days: int) -> Decimal | None:
    """Close-to-close return from the last bar on/before as_of to the last
    bar on/before as_of + forward_days -- but only if that end bar is
    actually near the target date. Without the tolerance check, an
    instrument whose price history simply stops partway through the window
    would silently return its LAST available bar as though it were the
    forward_days price: a 3-day return relabelled as a 180-day one, which
    is a wrong number that looks exactly like a right one."""
    target = date.fromordinal(as_of.toordinal() + forward_days)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT close FROM price_bars WHERE instrument_id = %s AND day <= %s "
            "ORDER BY day DESC LIMIT 1", (instrument_id, as_of))
        start_row = cur.fetchone()
        cur.execute(
            "SELECT day, close FROM price_bars WHERE instrument_id = %s AND day <= %s "
            "ORDER BY day DESC LIMIT 1", (instrument_id, target))
        end_row = cur.fetchone()
    if start_row is None or end_row is None:
        return None
    end_day, end_close = end_row
    if (target.toordinal() - end_day.toordinal()) > _FORWARD_LOOKUP_TOLERANCE_DAYS:
        return None
    start_close = start_row[0]
    if start_close == 0:
        return None
    return (end_close - start_close) / start_close


def run(conn: psycopg.Connection, instrument_ids: list[int], as_of_dates: list[date],
        forward_days: int) -> list[BacktestPoint]:
    points = []
    for as_of in as_of_dates:
        scores = combined_score.compute_scores(conn, instrument_ids, as_of)
        for s in scores:
            fr = _forward_return(conn, s.instrument_id, as_of, forward_days)
            if fr is None:
                continue
            points.append(BacktestPoint(
                instrument_id=s.instrument_id, as_of=as_of,
                composite_score=s.composite_score, forward_days=forward_days,
                forward_return=fr,
            ))
    return points


def store(conn: psycopg.Connection, points: list[BacktestPoint]) -> int:
    written = 0
    with conn.cursor() as cur:
        for p in points:
            cur.execute(
                """
                INSERT INTO backtest_results
                    (instrument_id, as_of, model_version, composite_score,
                     forward_days, forward_return)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (instrument_id, as_of, model_version, forward_days) DO UPDATE
                   SET composite_score = excluded.composite_score,
                       forward_return = excluded.forward_return,
                       computed_at = now()
                """,
                (p.instrument_id, p.as_of, MODEL_VERSION, p.composite_score,
                 p.forward_days, p.forward_return),
            )
            written += 1
    return written


def _ranks(values: list[float]) -> list[float]:
    """Average ranks, ties split evenly -- the standard Spearman convention.
    0-indexed ascending: the smallest value gets rank 0."""
    ordered = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and values[ordered[j + 1]] == values[ordered[i]]:
            j += 1
        avg_rank = (i + j) / 2
        for k in range(i, j + 1):
            ranks[ordered[k]] = avg_rank
        i = j + 1
    return ranks


def information_coefficient(points: list[BacktestPoint]) -> dict:
    """Spearman rank correlation between composite_score and forward_return,
    pooled across every point given. No scipy dependency -- rank both
    series, then a plain Pearson correlation of the ranks, which is what
    Spearman's rho actually is."""
    n = len(points)
    if n < 3:
        return {"n": n, "ic": None,
                "note": "fewer than 3 data points -- not enough to compute a "
                        "correlation at all, let alone trust one"}

    scores = [float(p.composite_score) for p in points]
    returns = [float(p.forward_return) for p in points]
    score_ranks = _ranks(scores)
    return_ranks = _ranks(returns)

    mean_s = sum(score_ranks) / n
    mean_r = sum(return_ranks) / n
    cov = sum((s - mean_s) * (r - mean_r) for s, r in zip(score_ranks, return_ranks))
    var_s = sum((s - mean_s) ** 2 for s in score_ranks)
    var_r = sum((r - mean_r) ** 2 for r in return_ranks)
    if var_s == 0 or var_r == 0:
        return {"n": n, "ic": None,
                "note": "no variation in score or return across the sample -- "
                        "correlation is undefined, not zero"}

    ic = cov / (var_s * var_r) ** 0.5
    return {
        "n": n,
        "ic": ic,
        "note": (f"n={n} (as_of, instrument) pairs. A small sample by "
                 "research standards -- read this as a checkable signal, "
                 "not a statistically powered claim."),
    }
