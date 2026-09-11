"""SEC EDGAR XBRL adapter — the first working source.

Why EDGAR, when the product is about Indian equities:

EDGAR's `companyfacts` API is **natively bitemporal**, which nothing else we
surveyed is. Every fact carries the period it describes AND the filing that
reported it, with that filing's date and accession number. The same period
appears once per filing that mentioned it. That is exactly the shape of our
store: valid time, transaction time, provenance.

Commercial APIs cannot do this (see 03a). They serve the current restated view,
so the original figure is gone the moment a company restates. EDGAR keeps every
filing forever, because that is what a filing archive is for.

And the licensing is unambiguous. From the SEC's own webmaster FAQ:

    "All Government-created content on sec.gov and EDGAR public filing content
    are free to access and reuse."

Plus a documented access policy: 10 requests/second, declared User-Agent.

WHAT THIS IS NOT
----------------
Eight Indian companies, not five hundred -- only ADR issuers file with the SEC.
Annual periods, in practice: foreign private issuers file 20-F annually, and
their quarterly 6-K filings are largely not XBRL-tagged. And the figures are as
filed with the SEC under IFRS or US-GAAP, not the Ind AS numbers filed in
India, so they will not match Screener.in.

So this is a stage-one source: real, legally clean, natively bitemporal data
that proves the whole pipeline end to end and reaches a public URL. A second
adapter for Indian filings goes behind the same interface later. That is what
architecture §2.2 exists for.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Iterator

from investment_intelligence.sources.base import (
    FilingDocument,
    InvalidFact,
    Rejection,
    ReportedFact,
)

USER_AGENT = "investment-intelligence (research) glvivekreddy@gmail.com"
COMPANYFACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# ---------------------------------------------------------------------------
# Concept mapping
# ---------------------------------------------------------------------------
# Two taxonomies appear across the Indian filers: `ifrs-full` for most of them,
# `us-gaap` for the ones that file as US domestic registrants. Same concept,
# different tag, so both are mapped onto our own vocabulary.
#
# This map is deliberately small and deliberately incomplete. Every entry is a
# claim that two things mean the same, and a wrong claim here is invisible in
# the output -- it produces a plausible number computed from the wrong input.
# Adding a line is cheap; adding a wrong line is expensive.
CONCEPTS: dict[str, dict[str, str]] = {
    "ifrs-full": {
        "RevenueFromContractsWithCustomers": "REVENUE",
        "Revenue": "REVENUE",
        "ProfitLoss": "NET_PROFIT",
        "ProfitLossBeforeTax": "PROFIT_BEFORE_TAX",
        "IncomeTaxExpenseContinuingOperations": "TAX_EXPENSE",
        "DepreciationAndAmortisationExpense": "DEPRECIATION",
        "FinanceCosts": "FINANCE_COST",
        "Assets": "TOTAL_ASSETS",
        "Equity": "TOTAL_EQUITY",
        "CashAndCashEquivalents": "CASH",
        "Inventories": "INVENTORY",
        "TradeAndOtherCurrentReceivables": "RECEIVABLES",
        "TradeAndOtherCurrentPayables": "PAYABLES",
        "CashFlowsFromUsedInOperatingActivities": "CF_OPERATING",
        "CashFlowsFromUsedInInvestingActivities": "CF_INVESTING",
        "CashFlowsFromUsedInFinancingActivities": "CF_FINANCING",
        "BasicEarningsLossPerShare": "EPS_BASIC",
    },
    "us-gaap": {
        "Revenues": "REVENUE",
        "RevenueFromContractWithCustomerExcludingAssessedTax": "REVENUE",
        "NetIncomeLoss": "NET_PROFIT",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": "PROFIT_BEFORE_TAX",
        "IncomeTaxExpenseBenefit": "TAX_EXPENSE",
        "DepreciationDepletionAndAmortization": "DEPRECIATION",
        "InterestExpense": "FINANCE_COST",
        "Assets": "TOTAL_ASSETS",
        "StockholdersEquity": "TOTAL_EQUITY",
        "CashAndCashEquivalentsAtCarryingValue": "CASH",
        "InventoryNet": "INVENTORY",
        "AccountsReceivableNetCurrent": "RECEIVABLES",
        "AccountsPayableCurrent": "PAYABLES",
        "NetCashProvidedByUsedInOperatingActivities": "CF_OPERATING",
        "NetCashProvidedByUsedInInvestingActivities": "CF_INVESTING",
        "NetCashProvidedByUsedInFinancingActivities": "CF_FINANCING",
        "EarningsPerShareBasic": "EPS_BASIC",
    },
}

# Units we accept as money. `shares`, `pure`, `USD/shares` and the rest are
# either not money or not comparable, and silently treating a per-share figure
# as a total is exactly the sort of error that looks plausible.
MONEY_UNITS = frozenset({"INR", "USD", "EUR", "GBP"})

# EDGAR reports a period as a span; we label it. Anything that does not fall in
# a recognised band is rejected rather than guessed at, because a mislabelled
# period propagates into every growth and margin metric computed from it.
_SPANS = (
    (80, 100, "Q"),
    (170, 195, "H"),
    (350, 400, "ANNUAL"),
)


def _fiscal_year(period_end: date) -> int:
    """The fiscal year a period belongs to, derived from its own end date.

    NOT EDGAR's `fy` field. That is the fiscal year of the *report* the fact
    appeared in, not of the period the fact describes -- so FY2019 revenue
    restated in a 2021 filing carries fy=2021. Using it would give one
    economic period different identities depending on which filing mentioned
    it, which breaks fact identity in the worst possible way: restatements
    stop being recognised as restatements and become new, separate facts.

    Nothing would look wrong. The store would just quietly hold three
    unrelated FY2019 revenues and `facts_as_of` would return all three.

    Indian fiscal years end 31 March, so a period ending 2019-03-31 is FY2019.
    A period ending in April-December belongs to the fiscal year ending the
    following March.
    """
    return period_end.year if period_end.month <= 3 else period_end.year + 1


def _period_type(start: date, end: date, fiscal_period: str | None) -> str | None:
    """Label a span, using EDGAR's own `fp` hint to pick the quarter number."""
    days = (end - start).days
    for low, high, kind in _SPANS:
        if low <= days <= high:
            if kind == "ANNUAL":
                return "ANNUAL"
            if kind == "H":
                return "H1" if fiscal_period in (None, "Q2", "H1") else "H2"
            # Quarterly: EDGAR's fp is Q1..Q4 or FY. Without a usable hint we
            # cannot say which quarter, and guessing would be worse than
            # skipping.
            return fiscal_period if fiscal_period in ("Q1", "Q2", "Q3", "Q4") else None
    return None


@dataclass
class EdgarSource:
    """Reads one company's XBRL facts and yields them grouped by filing.

    `fetch` is injectable so tests run against cached JSON rather than the
    live API. Testing a rate-limited public service by hammering it is both
    rude and unreliable.
    """

    source_id: str = "SEC_EDGAR"
    key_scheme: str = "SEC_CIK"
    cik_by_ref: dict[str, int] = field(default_factory=dict)
    fetch: object = None  # Callable[[int], dict]; defaults to the live API
    basis: str = "CONSOLIDATED"

    def _load(self, cik: int) -> dict:
        if self.fetch is not None:
            return self.fetch(cik)  # type: ignore[operator]
        request = urllib.request.Request(
            COMPANYFACTS.format(cik=cik), headers={"User-Agent": USER_AGENT}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())

    def fetch_filings(
        self, ref: str, since: date, until: date
    ) -> Iterator[FilingDocument | Rejection]:
        cik = self.cik_by_ref.get(ref)
        if cik is None:
            yield Rejection(instrument_ref=ref, detail="no CIK mapping",
                            reason="instrument is not a known SEC registrant")
            return

        try:
            payload = self._load(cik)
        except urllib.error.HTTPError as exc:
            # 404 means this registrant has no XBRL facts -- ICICI Bank is one.
            # A Rejection rather than an exception, so the backfill records it
            # and carries on to the next company.
            yield Rejection(instrument_ref=ref, detail=f"CIK {cik}",
                            reason=f"companyfacts unavailable: HTTP {exc.code}")
            return

        yield from self._to_documents(ref, payload, since, until)

    def _to_documents(
        self, ref: str, payload: dict, since: date, until: date
    ) -> Iterator[FilingDocument | Rejection]:
        """Regroup EDGAR's concept-major layout into filing-major documents.

        EDGAR nests facts under concept, then unit, then a flat list of
        occurrences each naming its filing. Our store is organised by filing,
        because provenance runs fact -> filing -> source. So this inverts the
        structure, keyed on accession number.
        """
        grouped: dict[tuple[str, str, str], dict[tuple, ReportedFact]] = {}
        rejections: list[Rejection] = []

        # Pass one: learn this company's real period boundaries from its FLOW
        # facts, which carry explicit start and end dates.
        #
        # Balance-sheet facts carry only an instant, and an instant cannot be
        # placed by arithmetic. An earlier version derived the fiscal year from
        # the date alone, which put both 2017-12-31 and 2018-03-31 in "FY2018
        # ANNUAL" -- so an interim balance sheet collided with the year-end
        # one, and the live backfill failed on nine of ten companies.
        #
        # So an instant is accepted only when it lands exactly on the end of a
        # period this company has actually reported. Anything else is an
        # interim balance we cannot place, and it is rejected rather than
        # guessed at.
        periods = self._flow_periods(payload)

        for taxonomy, concepts in payload.get("facts", {}).items():
            mapping = CONCEPTS.get(taxonomy)
            if mapping is None:
                continue  # `dei`, `srt`, `invest`: not financial statement data

            for concept_name, concept in concepts.items():
                line_item = mapping.get(concept_name)
                if line_item is None:
                    continue

                for unit, entries in concept.get("units", {}).items():
                    if unit not in MONEY_UNITS and line_item != "EPS_BASIC":
                        continue
                    currency = unit.split("/")[0]
                    if currency not in MONEY_UNITS:
                        continue

                    for entry in entries:
                        fact_or_rejection = self._to_fact(
                            ref, line_item, currency, entry, since, until, periods
                        )
                        if fact_or_rejection is None:
                            continue
                        if isinstance(fact_or_rejection, Rejection):
                            rejections.append(fact_or_rejection)
                            continue

                        fact = fact_or_rejection
                        doc_key = (entry["accn"], entry["filed"], entry.get("form", "20-F"))
                        # Fact identity within a filing. Two XBRL concepts can
                        # map to the same line item, and a filing can tag the
                        # same figure in several contexts. Keying the batch by
                        # identity collapses those before they reach the
                        # database, where they would collide on
                        # (identity, known_from) -- and a collision aborts the
                        # whole company's transaction.
                        fact_key = (fact.line_item, fact.currency, fact.basis,
                                    fact.fiscal_year, fact.period_type)
                        batch = grouped.setdefault(doc_key, {})
                        existing = batch.get(fact_key)
                        if existing is None:
                            batch[fact_key] = fact
                        elif existing.value != fact.value:
                            # Same identity, same filing, two different values.
                            # Not something to pick between silently: one of
                            # our concept mappings is wrong, or the filing
                            # tagged a segment figure as a total.
                            rejections.append(Rejection(
                                instrument_ref=ref,
                                detail=f"{fact.line_item} FY{fact.fiscal_year} "
                                       f"{fact.currency} in {entry['accn']}",
                                reason=f"same fact reported twice in one filing with "
                                       f"different values: {existing.value} vs {fact.value}",
                            ))

        for rejection in rejections:
            yield rejection

        for (accn, filed, form), batch in sorted(grouped.items(), key=lambda kv: kv[0][1]):
            facts = list(batch.values())
            # `filed` is EDGAR's own transaction time: the date this claim
            # entered the public record. It becomes our known_from, which is
            # why point-in-time queries over this data are truthful rather
            # than approximated by ingestion time.
            filed_at = datetime.combine(date.fromisoformat(filed), time(0, tzinfo=timezone.utc))
            yield FilingDocument(
                instrument_ref=ref,
                filing_type="ANNUAL_REPORT" if form.startswith("20-F") or form == "10-K"
                            else "QUARTERLY_RESULT",
                period_end=max(f.period_end for f in facts),
                filed_at=filed_at,
                source_ref=f"https://www.sec.gov/Archives/edgar/data/"
                           f"{payload['cik']}/{accn.replace('-', '')}/",
                content_hash=accn,
                facts=tuple(facts),
            )

    @staticmethod
    def _flow_periods(payload: dict) -> dict[date, tuple[int, str]]:
        """Map each reported period END to its (fiscal_year, period_type).

        Built from flow facts only, because they are the ones that state their
        own boundaries. This is what lets a balance-sheet instant be attached
        to the period it closes instead of to a period we invented.
        """
        out: dict[date, tuple[int, str]] = {}
        for taxonomy, concepts in payload.get("facts", {}).items():
            if taxonomy not in CONCEPTS:
                continue
            for concept in concepts.values():
                for entries in concept.get("units", {}).values():
                    for entry in entries:
                        if not entry.get("start"):
                            continue
                        start = date.fromisoformat(entry["start"])
                        end = date.fromisoformat(entry["end"])
                        label = _period_type(start, end, entry.get("fp"))
                        if label is None:
                            continue
                        # ANNUAL wins where a date ends both a year and a
                        # quarter: a year-end balance sheet belongs to the year.
                        if label == "ANNUAL" or end not in out:
                            out[end] = (_fiscal_year(end), label)
        return out

    def _to_fact(
        self, ref: str, line_item: str, currency: str, entry: dict,
        since: date, until: date, periods: dict[date, tuple[int, str]],
    ) -> ReportedFact | Rejection | None:
        end = date.fromisoformat(entry["end"])
        if not since <= end <= until:
            return None

        start_raw = entry.get("start")
        is_instant = start_raw is None

        if is_instant:
            placed = periods.get(end)
            if placed is None:
                # An interim balance with no reported period ending on the same
                # date. Attaching it to a guessed period would silently mix a
                # December balance into the March year-end.
                return Rejection(
                    instrument_ref=ref,
                    detail=f"{line_item} as at {end}",
                    reason="balance date matches no reported period end",
                )
            start = end
            fiscal_year, period_type = placed
        else:
            start = date.fromisoformat(start_raw)
            period_type = _period_type(start, end, entry.get("fp"))
            if period_type is None:
                return Rejection(
                    instrument_ref=ref,
                    detail=f"{line_item} {start}..{end}",
                    reason=f"unrecognised period span of {(end - start).days} days",
                )
            fiscal_year = _fiscal_year(end)

        try:
            return ReportedFact(
                line_item=line_item,
                value=Decimal(str(entry["val"])),
                basis=self.basis,
                fiscal_year=fiscal_year,
                period_type=period_type,
                period_start=start,
                period_end=end,
                currency=currency,
                is_instant=is_instant,
            )
        except InvalidFact as exc:
            return Rejection(instrument_ref=ref, detail=f"{line_item} {start}..{end}",
                             reason=str(exc))
