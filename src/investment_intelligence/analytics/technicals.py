"""Technical indicators, computed point-in-time from price_bars.

Same rules as factor_score.py, restated because they matter here too:

  - Every function reads only bars dated on or before `as_of`. A future bar
    sitting in the same table must never change a past indicator.
  - A missing indicator is None, never a default. An instrument with 40 days
    of history has no SMA_200, and reporting one anyway -- computed from
    whatever's there, silently short a window -- is a fabricated number
    wearing a real one's name.
  - This module computes VALUES, not verdicts. "RSI is 78" is a calculation.
    "RSI is 78, so sell" is the opinion docs/01-vision-and-scope.md's own
    test exists to keep this project on the right side of. Nothing here
    outputs a direction.

Two indicators (RSI, MACD) are approximations of their standard recursive
definitions, and that is a deliberate, disclosed trade rather than a bug:

  Wilder's RSI and an EMA are both defined recursively from the start of a
  price series, so the "true" value technically depends on the entire
  history before it. Recomputing that on every call does not scale and does
  not match what any point-in-time system can promise anyway -- an
  instrument's inception date is itself sometimes uncertain. Instead this
  module uses a bounded trailing window (250 sessions, roughly a year) long
  enough for an EMA's seeding transient to decay to a negligible influence,
  and documents the window size in every stored result so the number is
  reproducible rather than mysterious.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, getcontext

import psycopg

MODEL_VERSION = "technicals-v1"

# ~1 trading year. Long enough to seed EMA-26 (MACD) and settle its
# transient, and long enough for SMA_200. Chosen for headroom, not tuned
# against this data -- tuning a lookback on nine companies would be the same
# small-sample mistake factor_score.py's docstring already explains.
LOOKBACK_SESSIONS = 250

getcontext().prec = 28  # default is already this; explicit for sqrt() below


def price_series_asof(
    conn: psycopg.Connection, instrument_id: int, as_of: date, n: int = LOOKBACK_SESSIONS
) -> list[tuple[date, Decimal, Decimal, Decimal, int]]:
    """Trailing bars up to and including as_of, OLDEST FIRST (day, high, low,
    close, volume) -- the order every recursive calc below needs."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT day, high, low, close, volume FROM price_bars
            WHERE instrument_id = %s AND day <= %s
            ORDER BY day DESC LIMIT %s
            """,
            (instrument_id, as_of, n),
        )
        rows = cur.fetchall()
    return list(reversed(rows))


def sma(closes: list[Decimal], n: int) -> Decimal | None:
    if len(closes) < n:
        return None
    window = closes[-n:]
    return sum(window) / n


def rsi_14(closes: list[Decimal]) -> Decimal | None:
    """Simple-average RSI over the trailing 14 changes, not Wilder's
    recursive smoothing -- see module docstring. Needs 15 closes (14
    changes)."""
    if len(closes) < 15:
        return None
    diffs = [closes[i] - closes[i - 1] for i in range(len(closes) - 14, len(closes))]
    gains = [d for d in diffs if d > 0]
    losses = [-d for d in diffs if d < 0]
    avg_gain = sum(gains) / 14 if gains else Decimal(0)
    avg_loss = sum(losses) / 14 if losses else Decimal(0)
    if avg_loss == 0:
        return Decimal(100)
    rs = avg_gain / avg_loss
    return Decimal(100) - Decimal(100) / (1 + rs)


def _ema_series(values: list[Decimal], n: int) -> list[Decimal]:
    """EMA seeded with a simple average of the first `n` values -- the
    standard convention -- aligned so index 0 corresponds to values[n-1]."""
    if len(values) < n:
        return []
    multiplier = Decimal(2) / (n + 1)
    out = [sum(values[:n]) / n]
    for v in values[n:]:
        out.append((v - out[-1]) * multiplier + out[-1])
    return out


def macd(closes: list[Decimal]) -> tuple[Decimal, Decimal, Decimal] | tuple[None, None, None]:
    """Standard 12/26/9 MACD. Needs enough closes for EMA-26 to exist and
    for 9 MACD values to exist to seed the signal line -- 34 as an absolute
    floor, though LOOKBACK_SESSIONS gives it far more room to settle."""
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    if not ema26:
        return None, None, None
    # ema12 starts 14 sessions earlier than ema26 (index 11 vs 25 in the
    # original series); trim so both series line up on the same calendar day.
    ema12_aligned = ema12[len(ema12) - len(ema26):]
    macd_series = [a - b for a, b in zip(ema12_aligned, ema26)]
    signal_series = _ema_series(macd_series, 9)
    if not signal_series:
        return None, None, None
    macd_line = macd_series[-1]
    signal_line = signal_series[-1]
    return macd_line, signal_line, macd_line - signal_line


def bollinger(closes: list[Decimal], n: int = 20, k: int = 2) -> tuple[Decimal, Decimal, Decimal] | tuple[None, None, None]:
    """(upper, lower, %B). %B is where the latest close sits within the
    band: 0 at the lower band, 1 at the upper, negative or above 1 outside
    either. Population standard deviation (divide by n, not n-1), the
    conventional choice for Bollinger Bands."""
    if len(closes) < n:
        return None, None, None
    window = closes[-n:]
    mean = sum(window) / n
    variance = sum((x - mean) ** 2 for x in window) / n
    std = variance.sqrt()
    upper = mean + k * std
    lower = mean - k * std
    if upper == lower:
        return upper, lower, None
    percent_b = (closes[-1] - lower) / (upper - lower)
    return upper, lower, percent_b


def volume_ratio(volumes: list[int], n: int = 20) -> Decimal | None:
    """Today's volume over the n-day average INCLUDING today -- the
    conventional definition. > 1 means above-average activity."""
    if len(volumes) < n:
        return None
    window = volumes[-n:]
    avg = Decimal(sum(window)) / n
    if avg == 0:
        return None
    return Decimal(volumes[-1]) / avg


def rolling_range(highs: list[Decimal], lows: list[Decimal], closes: list[Decimal],
                   n: int) -> dict | None:
    """The n-day high/low EXCLUDING today, and whether today's close broke
    above or below that prior range. Excluding today is deliberate: a
    breakout is measured against the context before it, not against itself,
    which would make every day a "breakout" by definition."""
    if len(highs) < n + 1:
        return None
    prior_high = max(highs[-(n + 1):-1])
    prior_low = min(lows[-(n + 1):-1])
    breakout = "NONE"
    if closes[-1] > prior_high:
        breakout = "BREAKOUT_UP"
    elif closes[-1] < prior_low:
        breakout = "BREAKOUT_DOWN"
    return {"prior_high": prior_high, "prior_low": prior_low, "breakout": breakout}


def compute(conn: psycopg.Connection, instrument_id: int, as_of: date) -> dict | None:
    """Every indicator this module knows, for one instrument as of one date.
    Returns None entirely if there is no price history at all -- matching
    factor_score.compute_scores's "drop, don't fabricate a zero" rule."""
    rows = price_series_asof(conn, instrument_id, as_of)
    if not rows:
        return None

    closes = [r[3] for r in rows]
    highs = [r[1] for r in rows]
    lows = [r[2] for r in rows]
    volumes = [r[4] for r in rows]

    out: dict = {"as_of": as_of.isoformat(), "sessions_available": len(rows)}

    sma50, sma200 = sma(closes, 50), sma(closes, 200)
    if sma50 is not None:
        out["sma_50"] = str(sma50)
    if sma200 is not None:
        out["sma_200"] = str(sma200)
    if sma50 is not None and sma200 is not None:
        out["golden_cross"] = sma50 > sma200

    rsi = rsi_14(closes)
    if rsi is not None:
        out["rsi_14"] = str(rsi)

    macd_line, signal_line, hist = macd(closes)
    if macd_line is not None:
        out["macd"] = {"line": str(macd_line), "signal": str(signal_line),
                        "histogram": str(hist)}

    upper, lower, percent_b = bollinger(closes)
    if upper is not None:
        out["bollinger"] = {"upper": str(upper), "lower": str(lower),
                             "percent_b": str(percent_b) if percent_b is not None else None,
                             "window": 20}

    vr = volume_ratio(volumes)
    if vr is not None:
        out["volume_ratio_20d"] = str(vr)

    range_20 = rolling_range(highs, lows, closes, 20)
    if range_20 is not None:
        out["range_20d"] = {k: (str(v) if isinstance(v, Decimal) else v)
                             for k, v in range_20.items()}

    return out


def compute_and_store(conn: psycopg.Connection, instrument_ids: list[int],
                       as_of: date) -> int:
    written = 0
    with conn.cursor() as cur:
        for iid in instrument_ids:
            indicators = compute(conn, iid, as_of)
            if indicators is None:
                continue
            import json
            cur.execute(
                """
                INSERT INTO technical_indicators
                    (instrument_id, as_of, model_version, indicators)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (instrument_id, as_of, model_version) DO UPDATE
                   SET indicators = excluded.indicators, computed_at = now()
                """,
                (iid, as_of, MODEL_VERSION, json.dumps(indicators)),
            )
            written += 1
    return written
