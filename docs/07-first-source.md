# Phase 7 — First working source: SEC EDGAR

Status: **loaded and verified**
Last updated: 2026-09-11

Option 4 from [03a](03a-commercial-data-apis.md) §4, stage one. The plan was
"ten companies chosen for format consistency". What actually solved it was a
different source entirely.

---

## 1. Why EDGAR

`data.sec.gov/api/xbrl/companyfacts` is **natively bitemporal**, which nothing
else surveyed is. Every fact carries the period it describes, the filing that
reported it, that filing's date, and its accession number. The same period
appears once per filing that mentioned it.

That is our store's shape exactly: valid time, transaction time, provenance.
No other source gives the transaction-time axis at all — commercial APIs serve
the current restated view, so the original figure is gone the moment a company
restates.

Licensing is unambiguous. From the SEC's webmaster FAQ:

> "All Government-created content on sec.gov and EDGAR public filing content
> are free to access and reuse."

Access policy: 10 requests/second, declared User-Agent. We use one request
every 0.5s.

**XBRL also dissolved the format-consistency problem** that motivated
"ten companies chosen for format consistency". `ifrs-full:ProfitLoss` means
the same thing for a bank and an IT services firm, so sector variety costs
nothing here. With PDF parsing it would have cost everything.

## 2. What loaded

```
9 of 10 companies DONE, 1 SKIPPED, 0 FAILED
2,393 facts across 118 filings
2,406 repeats correctly collapsed to no new version
2,325 fact identities, of which 66 have more than one version
372 rejections, all of understood kinds
re-run: 0 facts, 0 filings, 0 companies — a genuine no-op
```

The SKIPPED company is ICICI Bank: `companyfacts` returns 404. Left in the
tracked set deliberately so the gap report has something real to report.

## 3. The wedge, on real data

Same query, two as-of dates, Wipro annual revenue in INR:

| As of 2019-12-31 | As of today |
|---|---|
| FY2016–FY2019, 4 rows | FY2016–FY2025, 10 rows |

The 2019 view excludes everything filed after that date. No lookahead, not
approximated — driven by the SEC's own `filed` dates.

And a genuine restatement, traceable to two documents. Sify Technologies,
total equity:

| Filed | Value | Document |
|---|---|---|
| 2023-06-28 | ₹17,145,688,000 | `.../000157587223001057/` |
| 2025-01-13 | ₹15,145,688,000 | `.../000157587225000052/` |

A ₹2bn downward restatement via a 20-F/A amendment. Screener.in shows only the
later figure. We hold both, and can answer what a screen in 2024 would have
returned.

## 4. Four bugs the live run found that fixtures did not

Worth recording, because all four were invisible-in-the-output failures and
three of them were mine.

### 4.1 Currency was not part of fact identity

Infosys reports FY2019 revenue in **both** USD and INR in the same 20-F. Under
003's key those were one fact with two values, so the second collided.
Migration 012 adds currency to the identity and to `facts_as_of`.

The failure was loud, which is why it was cheap. Had the constraint been
missing rather than wrong, one currency would have silently overwritten the
other and every metric would have mixed units.

### 4.2 Balance-sheet items were being dropped entirely

XBRL gives balance facts no start date, because a balance is an instant. The
first adapter skipped anything without a start — which silently removed
leverage from the metric set. A whole metric family gone, no error.

`ReportedFact.is_instant` now exists, mirroring `line_items.is_flow`, which the
schema had already got right.

### 4.3 Fiscal year was being taken from EDGAR's `fy` field

EDGAR's `fy` is the fiscal year of the *report*, not of the fact. FY2019
revenue restated in a 2021 filing carries `fy=2021`. Trusting it gives one
economic period different identities depending on which filing mentioned it —
so a restatement stops looking like a restatement and becomes a new fact.

Then deriving the year from the date alone was wrong too, differently: it put
`2017-12-31` and `2018-03-31` both in "FY2018 ANNUAL", so an interim balance
sheet collided with the year-end one. An instant is now accepted only when it
lands on the end of a period the company actually reported; anything else is
rejected rather than placed by guesswork.

### 4.4 `known_from` was ingestion time, not filing date

**The one that would have destroyed the product.** The backfill stamped every
document in a run with `datetime.now()`, so FY2018 revenue as reported in 2019
and as restated in 2021 both became "known from today" — the same fact at the
same instant. The unique constraint caught it. Without that constraint the
transaction-time axis would have silently collapsed and every historical query
would have returned today's view, correctly formatted and completely wrong.

`write_filing` now defaults `known_from` to the filing's own `filed_at`. The
override remains only for sources that genuinely cannot date their own claims.

## 5. Honest limitations

- **Eight Indian companies**, not five hundred. Only ADR issuers file with the
  SEC. This is a stage-one source, not the eventual one.
- **Annual periods in practice.** Foreign private issuers file 20-F annually
  and their 6-K quarterlies are largely not XBRL-tagged. Only Genpact, a US
  domestic filer, has real quarterly XBRL. Quarterly trends are much of what a
  screener is for, so this is a genuine product gap.
- **Figures are as filed with the SEC** under IFRS or US-GAAP, often in USD —
  not the Ind AS numbers filed in India. They will not match Screener.in, and
  the UI must say so rather than imply otherwise.
- **372 rejections.** Mostly interim balance dates and quarters whose `fp` hint
  is unusable. All counted, none imputed — but each is data we are not using,
  and the count should come down.
- **Index efficiency still unmeasured.** 2,393 rows is not enough volume to
  make `EXPLAIN` meaningful.

## 6. What Phase 7 delivered against its exit criteria

- [x] Universe loaded with target history depth *(for the stage-one universe)*
- [x] Re-running is a no-op — verified against the live source
- [x] Gaps enumerated, not hidden
- [ ] **Full NIFTY 500** — needs an Indian filings adapter, behind the same
      interface

Phase 7 exits for stage one. The Indian-source question from
[03-data-sources.md](03-data-sources.md) §6.3 remains open, and it is now a
widening problem rather than a blocking one: there is a working pipeline to
plug into.
