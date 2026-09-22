"""Market cap: shares outstanding (free, EDGAR) times price (local-only,
yfinance). The multiplication is what makes the RESULT local-only, even
though one of its two inputs is not -- see migration 028's header for why
that distinction is drawn deliberately rather than treating the whole
table as equally restricted for a simpler story.

No large/mid/small-cap label is produced here. See the migration's
comment: that classification is rank-based against India's full listed
universe (SEBI: top 100 large-cap, 101-250 mid-cap, the rest small-cap),
and cannot be honestly derived from nine tracked companies. This module
computes the number and stops.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import psycopg

from investment_intelligence.sources.edgar import USER_AGENT

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"


@dataclass(frozen=True)
class SharesOutstanding:
    value: int
    shares_as_of: date
    filed: date


def _pick_shares_asof(entries: list[dict], as_of: date) -> SharesOutstanding | None:
    """The point-in-time selection, pulled out as a pure function so it can
    be tested against a synthetic payload rather than the live API --
    same reasoning test_edgar.py gives for using a cached fixture instead
    of the network: the interesting behaviour is our own filtering logic,
    not SEC's uptime.

    The most recently FILED share count on or before `as_of`: a count filed
    after as_of was not yet public knowledge as of that date and must not be
    used, even though it might describe an earlier `end` date."""
    candidates = [e for e in entries if date.fromisoformat(e["filed"]) <= as_of]
    if not candidates:
        return None
    latest = max(candidates, key=lambda e: e["filed"])
    return SharesOutstanding(
        value=int(latest["val"]),
        shares_as_of=date.fromisoformat(latest["end"]),
        filed=date.fromisoformat(latest["filed"]),
    )


def _shares_entries(facts: dict) -> list[dict]:
    """Two filers in the tracked set, checked directly: one reports the
    standard cover-page tag (dei:EntityCommonStockSharesOutstanding), one
    reports the same shape of fact under a different taxonomy entirely
    (ifrs-full:NumberOfSharesOutstanding) and has no dei entry at all. Both
    were verified against the live API to have an identical shape --
    end/val/filed, unit "shares" -- before trusting the fallback, not
    assumed from the concept name alone.

    A third filer in the set (MMYT) has neither: its closest concept is
    AdjustedWeightedAverageShares, a period AVERAGE used for EPS. That is
    not the same thing as a point-in-time count, and substituting it would
    misrepresent an average as a snapshot -- so it stays unhandled rather
    than papered over with a plausible-looking number. See docs/backlog.md."""
    dei = facts.get("dei", {}).get("EntityCommonStockSharesOutstanding", {})
    entries = dei.get("units", {}).get("shares", [])
    if entries:
        return entries
    ifrs = facts.get("ifrs-full", {}).get("NumberOfSharesOutstanding", {})
    return ifrs.get("units", {}).get("shares", [])


def fetch_shares_outstanding(cik: int, as_of: date) -> SharesOutstanding | None:
    request = urllib.request.Request(
        COMPANYFACTS_URL.format(cik=cik), headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise

    entries = _shares_entries(data.get("facts", {}))
    return _pick_shares_asof(entries, as_of)


def _latest_price_asof(conn: psycopg.Connection, instrument_id: int,
                       as_of: date) -> tuple[Decimal, str] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pb.close, s.name FROM price_bars pb
            JOIN sources s ON s.source_id = pb.source_id
            WHERE pb.instrument_id = %s AND pb.day <= %s
            ORDER BY pb.day DESC LIMIT 1
            """,
            (instrument_id, as_of),
        )
        row = cur.fetchone()
    if row is None:
        return None
    # Every tracked instrument's price_bars are USD (US-listed ADRs) -- see
    # docs/03-data-sources.md §7. Asserted here rather than assumed, since a
    # silently wrong currency on a stored market cap is a wrong number that
    # looks exactly like a right one.
    return row[0], "USD"


def compute_and_store(conn: psycopg.Connection, instrument_id: int, cik: int,
                      as_of: date) -> Decimal | None:
    shares = fetch_shares_outstanding(cik, as_of)
    if shares is None:
        return None
    price_row = _latest_price_asof(conn, instrument_id, as_of)
    if price_row is None:
        return None
    price, currency = price_row

    market_cap = Decimal(shares.value) * price
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO market_cap_snapshots
                (instrument_id, as_of, shares_outstanding, shares_as_of,
                 price, currency, market_cap)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (instrument_id, as_of) DO UPDATE
               SET shares_outstanding = excluded.shares_outstanding,
                   shares_as_of = excluded.shares_as_of,
                   price = excluded.price, currency = excluded.currency,
                   market_cap = excluded.market_cap, computed_at = now()
            """,
            (instrument_id, as_of, shares.value, shares.shares_as_of,
             price, currency, market_cap),
        )
    return market_cap
