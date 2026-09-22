"""Writing price bars.

Simpler than `writer.py`'s fact-writing, because the versioning problem does
not exist here. A financial fact needs a conditional insert because the SAME
value re-appearing and a GENUINELY CHANGED value both have to be handled
correctly, and a restatement must never be silently dropped. A daily close
has no restatement case: today's fetch of 2026-06-30 either matches what is
already stored, or something is wrong, because no issuer revises yesterday's
print. So this is a plain `INSERT ... ON CONFLICT DO NOTHING` keyed on
(instrument_id, day) -- idempotent by construction, no version history to
reason about.

**Not wired into the public web app.** See migration 024 and
docs/03-data-sources.md §7: yfinance-sourced price data is research/local-use
only until a source with terms that permit public display replaces it. This
module and its CLI command exist to prove the pipeline, not to serve traffic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

from investment_intelligence.ingest.writer import UnknownInstrument, resolve_ref
from investment_intelligence.sources.base import Rejection
from investment_intelligence.sources.prices import PriceBar, PriceSource


@dataclass
class PriceWriteResult:
    bars_written: int = 0
    bars_unchanged: int = 0
    rejections: list[Rejection] = field(default_factory=list)

    def __init__(self) -> None:
        self.bars_written = 0
        self.bars_unchanged = 0
        self.rejections = []


_INSERT_BAR = """
    INSERT INTO price_bars
        (instrument_id, day, open, high, low, close, volume, source_id)
    VALUES (%(instrument_id)s, %(day)s, %(open)s, %(high)s, %(low)s,
            %(close)s, %(volume)s, %(source_id)s)
    ON CONFLICT (instrument_id, day) DO NOTHING
"""


def write_bars(
    conn: psycopg.Connection,
    source: PriceSource,
    ref: str,
    bars: list[PriceBar | Rejection],
) -> PriceWriteResult:
    """Writes one instrument's fetched bars. Unknown refs are rejections,
    matching how `writer.py` treats an unresolvable filing rather than
    raising and losing the rest of the run."""
    result = PriceWriteResult()
    try:
        instrument_id = resolve_ref(conn, source.key_scheme, ref)
    except UnknownInstrument:
        result.rejections.append(
            Rejection(instrument_ref=ref, detail="no instrument_external_ids row",
                       reason="UNKNOWN_INSTRUMENT")
        )
        return result

    with conn.cursor() as cur:
        for item in bars:
            if isinstance(item, Rejection):
                result.rejections.append(item)
                continue
            cur.execute(_INSERT_BAR, dict(
                instrument_id=instrument_id, day=item.day,
                open=item.open, high=item.high, low=item.low,
                close=item.close, volume=item.volume,
                source_id=source.source_id,
            ))
            if cur.rowcount == 1:
                result.bars_written += 1
            else:
                result.bars_unchanged += 1
    return result
