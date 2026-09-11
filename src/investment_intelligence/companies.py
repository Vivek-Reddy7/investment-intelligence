"""Stage-one tracked set: Indian companies that file with the SEC.

Ten entries chosen because they are the Indian registrants whose XBRL facts
EDGAR actually serves, not because ten is a target. Per 03a §5 the plan was
"ten companies chosen for format consistency" — and XBRL dissolves that
problem, because `ifrs-full:ProfitLoss` means the same thing for a bank and an
IT services firm. So sector variety here costs nothing, where with PDF parsing
it would have cost everything.

ISIN is deliberately absent. These companies have Indian ISINs, but we have
not verified them, and inventing an identifier is precisely the bug
002_instruments.sql exists to prevent. Migration 011 made ISIN optional for
this reason; it gets populated when an Indian source supplies it.

ICICI Bank is included and expected to fail: `companyfacts` returns 404 for it.
That is left in on purpose, so the gap report has something real to report and
the "one company failing must not end the run" path is exercised against a
live source rather than only a fixture.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Company:
    cik: int
    us_ticker: str
    name: str
    note: str = ""


STAGE_ONE: tuple[Company, ...] = (
    Company(1067491, "INFY", "Infosys Limited", "IT services, IFRS, USD"),
    Company(1123799, "WIT",  "Wipro Limited", "IT services, IFRS, INR"),
    Company(1135951, "RDY",  "Dr Reddy's Laboratories", "Pharma, IFRS, INR"),
    Company(1144967, "HDB",  "HDFC Bank Limited", "Bank, US-GAAP, INR"),
    Company(1094324, "SIFY", "Sify Technologies", "IT/telecom, IFRS, has 20-F/A amendments"),
    Company(1495153, "MMYT", "MakeMyTrip Limited", "Travel, IFRS, USD"),
    Company(1516899, "YTRA", "Yatra Online", "Travel, IFRS, has 20-F/A amendments"),
    Company(1398659, "G",    "Genpact Limited", "US domestic filer: 10-K/10-Q, real quarterly XBRL"),
    Company(1103838, "IBN",  "ICICI Bank Limited", "EXPECTED FAIL: companyfacts 404"),
    Company(1557860, "GLOB", "Globant S.A.", "Not Indian; included as a control on the adapter"),
)
