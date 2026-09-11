# Phase 9 — Analytics and Screening

Status: **complete**
Last updated: 2026-09-11

Nine metrics, point-in-time screening, provenance to the source document.
118 tests. Verified against the real EDGAR data loaded in Phase 7.

---

## 1. Metrics

Nine, in four families. No valuation metrics: those need prices, which Phase 3
put behind the private path.

| Family | Metric | Definition |
|---|---|---|
| Profitability | `NET_MARGIN` | NET_PROFIT / REVENUE |
| | `ROE` | NET_PROFIT / TOTAL_EQUITY |
| | `ROA` | NET_PROFIT / TOTAL_ASSETS |
| | `EFFECTIVE_TAX` | TAX_EXPENSE / PROFIT_BEFORE_TAX |
| Leverage | `LIAB_TO_EQUITY` | (TOTAL_ASSETS − TOTAL_EQUITY) / TOTAL_EQUITY |
| | `EQUITY_RATIO` | TOTAL_EQUITY / TOTAL_ASSETS |
| Growth | `REVENUE_GROWTH` | REVENUE / prior-year REVENUE − 1 |
| | `PROFIT_GROWTH` | NET_PROFIT / prior-year NET_PROFIT − 1 |
| Cash | `CASH_CONVERSION` | CF_OPERATING / NET_PROFIT |

## 2. Three decisions that could each have been done the easy way

### 2.1 One definition, two access paths

The formulas live in `metrics_as_of()` and nowhere else. The materialised
`metric_values` table is populated by calling that same function with `now()`.

Had the live view and the historical view carried separate definitions, they
would drift — and the drift would present as *a screen giving different
answers depending on which date you asked about*, which is indistinguishable
from the feature working correctly. A test asserts the two agree row for row.

### 2.2 Currency is part of the computation key

Infosys reports FY2019 revenue in both USD and INR. A net margin taking INR
profit over USD revenue is a plausible-looking number that means nothing, so
metrics are computed per currency and the prior-year join for growth also
matches on currency — otherwise a growth rate would be partly an exchange-rate
artefact.

### 2.3 An undefined ratio is absent, not a number

Return on equity with negative equity. Growth from a loss. A margin on zero
revenue. Cash conversion against a loss. These are undefined, not large, and a
screener that emits them produces rankings led by companies whose figures are
broken.

Every denominator is guarded, and `metrics_as_of` returns no row rather than a
misleading one. One deliberate asymmetry: `EQUITY_RATIO` is still computed when
equity is negative, because equity-to-assets of −0.2 is meaningful — it says
insolvent. `LIAB_TO_EQUITY` is not, because liabilities over negative equity is
nonsense. The guard is on the denominator that actually matters in each case.

## 3. Provenance

`metrics_as_of` returns an `inputs bigint[]` of the exact `fact_id`s each value
was computed from. That completes the chain the Phase 1 promise needs:

```
metric → facts → filing → source → licence note
```

`screen.explain()` walks it. Run against the real data:

```
PROVENANCE  INFY  NET_MARGIN = 18.6%
   NET_PROFIT   USD    2,200,000,000   FY2019  reported 2019-06-19
      https://www.sec.gov/Archives/edgar/data/1067491/000156459019022837/
   REVENUE      USD   11,799,000,000   FY2019  reported 2019-06-19
      https://www.sec.gov/Archives/edgar/data/1067491/000156459019022837/
```

A reader can open that document and check the figures. `metric_values` also
records `computed_at` separately from `as_of`: a row may be recomputed today
for the world as known at some other date, and both are needed to explain a
number.

## 4. Screening

`screen(conn, criteria, fiscal_year=..., as_of=..., on=...)`.

**Two parameters, both required for a truthful historical screen.** `as_of`
filters the *facts* to those known by then, so a figure restated afterwards is
not used. `on` filters the *universe* to companies reporting by then.

Using only `as_of` would screen today's universe with then-known figures,
which still leaks survivorship. Using only `on` would screen the right
companies with restated figures, which leaks lookahead. Both are needed and
the API takes both.

**Each criterion is its own `EXISTS`.** A single pivoted row per company would
be tidier SQL and would silently pass a company missing one of the screened
metrics on a NULL. A company with no ROE now fails an ROE screen rather than
slipping through.

**Operators come from an allow-list**, and thresholds must be `Decimal`. A
screen is user input reaching a `WHERE` clause; an unknown metric code raises
rather than returning an empty result, because "no matches" is a perfectly
ordinary answer and would hide the typo.

## 5. The bug running it found

The first real screen returned **zero matches for every criterion at every
date**. Metrics were correct and facts were loaded. The universe was empty.

`universe_as_of` selected from `instrument_status_as_of`, which returns no row
for an instrument with no lifecycle events — and the EDGAR adapter ingests
facts and filings but no lifecycle events. So every company sat outside the
universe.

It failed closed, which is the better direction. It was still a bad bug,
because *"no companies match your criteria"* is an entirely ordinary thing for
a screener to say. Nothing looked broken.

The logic was also wrong on its own terms: **absence of a delisting is not
absence of a company.** Migration 014 inverts it. Membership is driven by
evidence the company exists and reports — a filing — and lifecycle events only
ever subtract. A source that supplies no lifecycle data now produces a working
product instead of an empty one.

## 6. Verified on real data

```
SCREEN  net margin >= 10%  AND  ROE >= 12%   |  FY2019, USD
  INFY   margin 18.6%   roe 23.4%
  WIT    margin 15.4%   roe 15.8%
  HDB    margin 37.0%   roe 13.5%
  RDY    margin 12.2%   roe 13.4%
```

Both as-of dates return the same four here. **That is not evidence the wedge
works** — it means these companies did not restate FY2019 across the
thresholds. The evidence is the unit tests, where a company restated from a
10% margin to 2% is included as-of the earlier date and excluded today, and
the Sify restatement found in Phase 7.

## 7. Limitations

- **Annual only in practice.** Genpact is the only loaded company with real
  quarterly XBRL, so quarterly screening is untested against volume.
- **No peer comparison yet.** The exit criterion mentions it; grouping needs a
  sector classification we have no source for. Deferred and recorded in the
  backlog rather than faked.
- **No valuation metrics.** Needs prices — Phase 21.
- **Index efficiency unmeasured.** 1,404 metric rows over 9 companies is not
  enough volume for `EXPLAIN` to mean anything. A historical screen
  recomputes the whole pivot; that cost is real and unquantified.

---

## Exit criteria

- [x] Derived metrics with provenance — `inputs` names the exact facts
- [x] Multi-criteria screening
- [x] As-of-date screening: excludes companies not yet reporting, uses
      then-reported figures
- [x] Every metric names its inputs, period and computation time
- [ ] Peer comparison — deferred, no sector source
