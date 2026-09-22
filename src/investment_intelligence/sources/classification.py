"""SIC classification, from SEC EDGAR's submissions API.

A separate, lightweight fetcher rather than an extension of
`sources/edgar.py`'s `EdgarSource`. That adapter's whole shape is built
around a fiscal period, a basis and a value -- `ReportedFact` -- because a
revenue figure genuinely has those three things. A filer's SIC code has
none of them: it is not reported for a period, it does not have a
standalone/consolidated basis, and treating it as a `ReportedFact` would
mean inventing values for fields that do not apply, the same "make the type
fit" mistake `sources/prices.py`'s docstring already declined to make for a
different pair of types.

`data.sec.gov/submissions/` is the same host, and the same SEC webmaster FAQ
licence already covers it (docs/03-data-sources.md, and the SEC_EDGAR row in
`sources`) -- this is not a new licensing question, just a different
endpoint on an already-cleared domain. Uses the standard library, matching
`sources/edgar.py`: no new HTTP client dependency for one more GET.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from investment_intelligence.sources.edgar import USER_AGENT

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# Coarse ranges over SIC's ~1,000 codes into a handful of broad sectors.
# Ranges are the standard, widely-published SIC division boundaries (U.S.
# Dept of Labor's SIC manual), not tuned against the tracked set -- the same
# "do not fit a lookback window to nine companies" discipline as
# factor_score.py and technicals.py, applied to a classification scheme
# instead of a numeric window.
SIC_SECTOR_RANGES: tuple[tuple[int, int, str], ...] = (
    (100, 999, "Agriculture, Forestry, Fishing"),
    (1000, 1499, "Mining"),
    (1500, 1799, "Construction"),
    (2000, 3999, "Manufacturing"),
    (4000, 4899, "Transportation, Communications, Utilities"),
    (4900, 4999, "Utilities"),
    (5000, 5199, "Wholesale Trade"),
    (5200, 5999, "Retail Trade"),
    (6000, 6799, "Financial Services"),
    (7000, 8999, "Services"),
    (9100, 9999, "Public Administration"),
)


def sector_for_sic(sic_code: str) -> str:
    try:
        code = int(sic_code)
    except (TypeError, ValueError):
        return "Unclassified"
    for lo, hi, sector in SIC_SECTOR_RANGES:
        if lo <= code <= hi:
            return sector
    return "Unclassified"


@dataclass(frozen=True)
class SicClassification:
    cik: int
    sic_code: str
    sic_description: str
    sector: str


def fetch_sic(cik: int) -> SicClassification | None:
    """One filer's SIC classification, or None if EDGAR has nothing for
    this CIK -- mirrors how the rest of this project treats a source that
    genuinely has no data, rather than raising."""
    request = urllib.request.Request(
        SUBMISSIONS_URL.format(cik=cik), headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise

    sic_code = data.get("sic")
    if not sic_code:
        return None
    return SicClassification(
        cik=cik,
        sic_code=str(sic_code),
        sic_description=data.get("sicDescription", ""),
        sector=sector_for_sic(str(sic_code)),
    )


def fetch_all(ciks: list[int], *, delay_seconds: float = 0.5) -> list[SicClassification]:
    """Sequential, rate-limited -- the same 0.5s-between-requests posture
    `ingest/backfill.py` already uses against the same host, restated here
    because this is a second, independent caller hitting it."""
    results = []
    for i, cik in enumerate(ciks):
        if i > 0:
            time.sleep(delay_seconds)
        classification = fetch_sic(cik)
        if classification is not None:
            results.append(classification)
    return results
