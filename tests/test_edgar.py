"""SEC EDGAR adapter tests.

Run against a cached `companyfacts` response for Infosys, not the live API.
Hammering a rate-limited public service in a test suite is both rude and
unreliable, and the interesting assertions are about our interpretation of the
payload rather than about the network.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

import pytest

from investment_intelligence.sources.base import FilingDocument, InvalidFact, Rejection, ReportedFact
from investment_intelligence.sources.edgar import EdgarSource, _fiscal_year, _period_type

FIXTURE = Path(__file__).parent / "fixtures" / "edgar_infy.json"
INFY_REF = "1067491"   # SEC CIK, the scheme EdgarSource speaks
INFY_CIK = 1067491
WIDE = (date(2010, 1, 1), date(2030, 1, 1))


@pytest.fixture(scope="module")
def payload() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def source(payload):
    return EdgarSource(cik_by_ref={INFY_REF: INFY_CIK}, fetch=lambda cik: payload)


@pytest.fixture
def documents(source) -> list[FilingDocument]:
    return [d for d in source.fetch_filings(INFY_REF, *WIDE)
            if isinstance(d, FilingDocument)]


# ---------------------------------------------------------------------------
# Fiscal year derivation — the bug this nearly shipped with
# ---------------------------------------------------------------------------

def test_fiscal_year_comes_from_the_period_not_the_filing(payload):
    """EDGAR's `fy` is the fiscal year of the REPORT, not of the fact.

    A 2021 filing restating FY2019 revenue carries fy=2021. Trusting it gives
    one economic period different identities depending on which filing
    mentioned it -- so a restatement stops looking like a restatement and
    becomes a separate fact. Nothing errors; the store just quietly holds
    three unrelated FY2019 revenues.

    This test compares our derivation against EDGAR's field and asserts they
    disagree, because if they ever agreed everywhere the bug would be
    invisible again.
    """
    revenue = payload["facts"]["ifrs-full"]["RevenueFromContractsWithCustomers"]
    disagreements = 0
    for entries in revenue["units"].values():
        for entry in entries:
            end = date.fromisoformat(entry["end"])
            if entry.get("fy") and entry["fy"] != _fiscal_year(end):
                disagreements += 1
    assert disagreements > 0, "EDGAR's fy agreed with ours everywhere; check the fixture"


@pytest.mark.parametrize("period_end,expected", [
    (date(2019, 3, 31), 2019),   # Indian FY ends 31 March
    (date(2020, 3, 31), 2020),
    (date(2019, 12, 31), 2020),  # Dec sits in the FY ending next March
    (date(2019, 6, 30), 2020),
])
def test_fiscal_year_boundary(period_end, expected):
    assert _fiscal_year(period_end) == expected


def test_one_identity_per_economic_period_across_filings(documents):
    """The property the fiscal-year fix buys: the same period reported by
    several filings resolves to ONE fact identity with several versions."""
    identities = defaultdict(set)
    for doc in documents:
        for fact in doc.facts:
            if fact.line_item == "NET_PROFIT" and not fact.is_instant:
                identities[(fact.fiscal_year, fact.currency)].add(doc.content_hash)

    shared = {k: v for k, v in identities.items() if len(v) > 1}
    assert shared, "no period was reported by more than one filing"


# ---------------------------------------------------------------------------
# Balance-sheet items
# ---------------------------------------------------------------------------

def test_balance_sheet_items_are_not_dropped(documents):
    """XBRL gives balance facts no start date. An earlier version of this
    adapter skipped them, which would have silently removed leverage from the
    metric set -- a whole metric family missing, with no error anywhere."""
    instants = [f for d in documents for f in d.facts if f.is_instant]
    assert instants
    assert {"TOTAL_ASSETS", "TOTAL_EQUITY"} <= {f.line_item for f in instants}


def test_an_instant_has_a_zero_length_period(documents):
    for doc in documents:
        for fact in doc.facts:
            if fact.is_instant:
                assert fact.period_start == fact.period_end


def test_flow_items_keep_a_real_span(documents):
    for doc in documents:
        for fact in doc.facts:
            if not fact.is_instant:
                assert fact.period_end > fact.period_start


def test_an_instant_with_a_span_is_refused():
    with pytest.raises(InvalidFact, match="period_start == period_end"):
        ReportedFact(
            line_item="TOTAL_ASSETS", value=__import__("decimal").Decimal("1"),
            basis="CONSOLIDATED", fiscal_year=2026, period_type="ANNUAL",
            period_start=date(2025, 4, 1), period_end=date(2026, 3, 31),
            is_instant=True,
        )


# ---------------------------------------------------------------------------
# Parsing, provenance, and windows
# ---------------------------------------------------------------------------

def test_real_infosys_data_parses(source):
    items = list(source.fetch_filings(INFY_REF, *WIDE))
    docs = [i for i in items if isinstance(i, FilingDocument)]
    assert len(docs) >= 8
    assert sum(len(d.facts) for d in docs) > 250


def test_rejections_are_only_of_understood_kinds(source):
    """Rejections are expected, and that is the point -- they are counted, not
    silent. What must not happen is a rejection we cannot explain.

    This asserts the categories rather than a count, so the number can move as
    the concept map grows without the test becoming noise, while a NEW kind of
    rejection still fails loudly and gets understood before it is accepted.
    """
    known = {
        "balance date matches no reported period end",
        "unrecognised period span",
        "same fact reported twice in one filing",
    }
    rejects = [i for i in source.fetch_filings(INFY_REF, *WIDE)
               if isinstance(i, Rejection)]
    unexplained = [r for r in rejects
                   if not any(r.reason.startswith(k) for k in known)]
    assert unexplained == [], f"new rejection kind: {unexplained[:3]}"


def test_unplaceable_balance_dates_are_rejected_not_guessed(source):
    """The bug the live backfill found. Deriving a fiscal year from a balance
    date alone put 2017-12-31 and 2018-03-31 both in "FY2018 ANNUAL", so an
    interim balance sheet collided with the year-end one. Now an instant is
    only accepted when it lands on a period the company actually reported."""
    rejects = [i for i in source.fetch_filings(INFY_REF, *WIDE)
               if isinstance(i, Rejection)]
    assert any("balance date matches no reported period end" in r.reason
               for r in rejects)


def test_every_document_carries_its_accession_and_a_filing_url(documents):
    """Provenance: the Phase 1 promise. A fact names its filing, and the filing
    names a document a reader can open."""
    for doc in documents:
        assert doc.content_hash.count("-") == 2, "accession number expected"
        assert doc.source_ref.startswith("https://www.sec.gov/Archives/edgar/data/")


def test_filed_at_is_the_sec_filing_date(documents):
    """EDGAR's `filed` is the date the claim entered the public record, which
    is a true transaction time. Using ingestion time instead would make every
    point-in-time answer an approximation of when WE looked, not of when the
    world could have known."""
    for doc in documents:
        assert doc.filed_at.tzinfo is not None
        assert date(2010, 1, 1) < doc.filed_at.date() < date(2030, 1, 1)


def test_the_window_is_respected(source):
    narrow = [d for d in source.fetch_filings(INFY_REF, date(2019, 1, 1), date(2020, 12, 31))
              if isinstance(d, FilingDocument)]
    for doc in narrow:
        for fact in doc.facts:
            assert date(2019, 1, 1) <= fact.period_end <= date(2020, 12, 31)


def test_an_unmapped_isin_is_rejected_not_raised(source):
    """One unknown company must not end a 500-company backfill."""
    items = list(source.fetch_filings("9999999", *WIDE))
    assert len(items) == 1
    assert isinstance(items[0], Rejection)
    assert "not a known SEC registrant" in items[0].reason


def test_per_share_and_ratio_units_do_not_become_money(documents):
    """`pure`, `shares` and `USD/shares` are not comparable to a total.
    Treating a per-share figure as revenue produces a plausible wrong number."""
    for doc in documents:
        for fact in doc.facts:
            assert fact.currency in {"INR", "USD", "EUR", "GBP"}


# ---------------------------------------------------------------------------
# Period labelling
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("start,end,fp,expected", [
    (date(2019, 4, 1), date(2020, 3, 31), "FY", "ANNUAL"),
    (date(2019, 7, 1), date(2019, 9, 30), "Q2", "Q2"),
    (date(2019, 4, 1), date(2019, 9, 30), "Q2", "H1"),
    (date(2019, 7, 1), date(2019, 9, 30), "FY", None),   # quarter, unknown which
    (date(2019, 1, 1), date(2019, 5, 15), "FY", None),   # 134 days: no band
])
def test_period_labelling(start, end, fp, expected):
    assert _period_type(start, end, fp) == expected


def test_an_unrecognised_span_is_rejected_rather_than_guessed():
    """A 134-day "quarter" means the adapter misread something, and every
    growth and margin metric computed from it inherits the error."""
    assert _period_type(date(2019, 1, 1), date(2019, 5, 15), "Q1") is None
