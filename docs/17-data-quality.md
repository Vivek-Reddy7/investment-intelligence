# Phase 17 — Data Quality and Reconciliation

Status: **complete**
Last updated: 2026-09-11

202 tests. Six checks, rejection classification, and a report that cannot lie
about whether it ran.

---

## 1. What this phase guards that nothing else did

Everything before it guards the **store**: append-only triggers, versioning,
provenance, privileges. None of that catches a **mis-mapped concept**, which
produces a fact that is well-formed, correctly stored, traceable to a real SEC
document — and wrong.

`edgar.py`'s concept map says so in a comment: *"a wrong claim here is
invisible in the output — it produces a plausible number computed from the
wrong input."* These checks are the attempt to make it visible.

## 2. The approach: accounting identities

A financial statement is internally redundant, and that redundancy is free
error detection needing no second source.

| Check | Identity | Severity |
|---|---|---|
| `TAX_IDENTITY` | pbt − tax = net profit, within 2% | WARN |
| `EQUITY_EXCEEDS_ASSETS` | equity ≤ assets, always | ERROR |
| `NEGATIVE_REVENUE` | revenue ≥ 0 | ERROR |
| `FX_INCONSISTENT` | one period in two currencies implies one rate | WARN |
| `INCOMPLETE_PERIOD` | revenue present ⇒ profit present | WARN |
| `IMPLAUSIBLE_TAX_RATE` | 0 ≤ effective rate ≤ 60% | WARN |

**Severity is not decoration.** ERROR means the data cannot be right. WARN
means it is unusual and a human should look. Emitting everything as ERROR
trains people to ignore the report, which is worse than having no report.

The tax identity tolerates 2% because minority interests and discontinued
operations legitimately break it. Flagging those would bury the real breaks in
noise — there is a test asserting a 1% break passes.

### 2.1 Cross-validation without a second source

`FX_INCONSISTENT` is the one worth pointing at. We have exactly one source, so
conventional cross-source validation is impossible.

But a company reporting the same period in both INR and USD has **told us the
same story twice**. Revenue/revenue and profit/profit must imply the same
exchange rate. If they diverge, one of the two figures belongs to a different
period, basis, or line item.

On real data this check evaluated **43 currency pairs and found zero
inconsistencies** — which is meaningful evidence that the concept mapping is
sound, in a way no amount of reading the code would give.

It also corroborated something. Seven `IMPLAUSIBLE_TAX_RATE` warnings turned
out to be negative effective rates — tax credits. Dr Reddy's FY2020 shows
−7.95% in USD and −8.13% in INR; Yatra FY2025 shows −1.21 in both. **The two
currencies agreeing is itself evidence these are genuinely reported credits
rather than mapping errors.**

## 3. A report that cannot lie about whether it ran

The first quality run reported zero `TAX_IDENTITY` findings. That is either
"the data is sound" or "the check had no inputs and evaluated nothing", and
the report could not tell them apart. It took a hand-written query to
establish that 183 of 185 periods really were checked.

**A quality report that cannot distinguish clean data from an inert check is
worse than no report, because it is actively reassuring.** So
`quality_coverage()` reports findings *and* evaluable periods, always:

```
CHECKS
  ok EQUITY_EXCEEDS_ASSETS    ERROR clean over 157 periods
  ok NEGATIVE_REVENUE         ERROR clean over 181 periods
  ok FX_INCONSISTENT          WARN  clean over 43 periods
   ~ IMPLAUSIBLE_TAX_RATE     WARN  7 finding(s) over 157 periods
  ok INCOMPLETE_PERIOD        WARN  clean over 181 periods
  ok TAX_IDENTITY             WARN  clean over 183 periods
```

An inert check prints `?? INERT — no periods could be evaluated`, and the CLI
exits non-zero for it exactly as it does for an ERROR finding.

## 4. The 372 rejections, finally readable

Phase 7 rejected 372 items, recorded the count in
`ingestion_runs.rows_rejected`, and threw every reason away — they lived in a
report object that went out of scope. So we knew we were discarding data and
not what, or why. That is an admission, not a report.

Rejections are now persisted and **classified**. Classification is the point:
reasons carry values and dates ("a span of 272 days"), so grouping on raw text
gives 372 groups of one. The class turns a count into a decision.

```
UNRECOGNISED_PERIOD_SPAN     366  across  2 instruments
UNPLACEABLE_BALANCE_DATE       5  across  5 instruments
SOURCE_UNAVAILABLE             1  across  1 instrument
```

**Update, 2026-09-11.** Both parser gaps below were then fixed, and the report
now reads:

```
YTD_NOT_STORED               151  across  1 instrument
UNPLACEABLE_BALANCE_DATE       5  across  5 instruments
SOURCE_UNAVAILABLE             1  across  1 instrument
```

215 facts recovered, and the 151 reclassified from a parse failure to a
deliberate decision. Actual failures: six. See [§8](#8-what-the-classification-led-to).

**366 of 372 rejections come from two companies.** Before classification "372
rejections" read as a broad data-quality problem. It is one narrow parser gap.
Digging in gives the precise diagnosis:

- **151 rejections, spans of 272–273 days, all Genpact.** Nine-month
  year-to-date periods. US 10-Q filers report cumulative YTD and our span
  bands have no 9-month entry.
- **~215 rejections, spans of 89–91 days.** Genuine quarters that *matched*
  the quarterly band, then were rejected because EDGAR's `fp` field said `FY`
  rather than `Q1`–`Q4`. The quarter is derivable from `period_end` against
  the company's fiscal year end — deterministic, and better than trusting
  `fp`.

Both are Phase 7 adapter work surfacing here, so they went to
[`backlog.md`](backlog.md) rather than expanding this phase. The second is
~215 facts currently discarded that we could be using.

A third finding fell out: **`_fiscal_year` assumes a March year end.** Correct
for the Indian filers, wrong for Genpact, which is a December filer. Its FY
labels should not be trusted.

`UNCLASSIFIED` is checked rather than assumed empty — a growing bucket means
the source started rejecting things for a new reason and nobody noticed.

## 5. Quality is itself as-of dated

`quality_as_of(as_of)`, like everything else (invariant 12). So *"was the data
we were serving in 2020 sound?"* is a question this product can answer about
itself. A test seeds a corruption, corrects it later, and asserts the
corruption is visible as of a date in between and gone afterwards.

## 6. What the privilege test forced

Migration 018 granted `DELETE` on `ingestion_rejections`, following the
`metric_values` pattern. The DELETE allow-list test from Phase 14 caught it
and made the difference explicit.

`metric_values` is genuinely a cache — every row recomputable by calling
`metrics_as_of()`. `ingestion_rejections` is not: **the rejected items are
precisely the ones that never became facts**, so a rejection is not derivable
from the facts. Deleting one destroys the only evidence we ever saw that data
and declined it.

Migration 019 revokes it. Insert-only. That is a Phase 14 test paying for
itself two phases later.

## 7. Limitations

- **No true cross-source validation.** One source. §2.1 is the best available
  substitute and it is genuinely useful, but two independent sources
  disagreeing is stronger evidence than one source being internally
  consistent.
- **No corporate-action discontinuity detection.** The exit criterion mentions
  it; with no prices and no corporate-action data ingested, there is nothing
  to check a discontinuity against. Recorded in the backlog rather than faked.
- **Checks are single-period.** Nothing yet flags a metric that jumps
  implausibly year on year, which is where a wrong-period pairing would show.
- **Tolerances are judgement, not measurement.** 2% for the tax identity and
  5% for FX were chosen as plausible, then confirmed to produce no false
  positives on 183 and 43 periods respectively. That is weak evidence at this
  sample size.
- **Findings are not triaged.** Nothing records "reviewed, this is a genuine
  tax credit", so the same seven warnings will reappear on every run.

---

## Exit criteria

- [x] Fundamentals sanity rules — six checks, severity-graded
- [x] Cross-validation — via dual-currency reporting, §2.1
- [x] A reconciliation report — `cli quality`, with coverage
- [x] **A seeded corruption is caught by the checks rather than by a user** —
      six seeded-corruption tests, each a plausible mistake that passes
      boundary validation
- [ ] Corporate-action discontinuity detection — deferred, no data to check
      against

---

## 8. What the classification led to

Naming the rejection classes was the whole return on this phase. Two of the
three classes turned out to be one parser gap and one unstated decision, and
both were fixed within the day:

**~215 quarters recovered.** Spans of 89–91 days matched the quarterly band
and were rejected anyway, because the code asked EDGAR's `fp` field which
quarter it was and `fp` frequently reads `FY` on a quarterly fact. The quarter
is a deterministic function of the period end and the company's fiscal year
end, so it is now derived. The store went from **zero** non-annual facts to
551.

**The fiscal calendar is now learned per company.** `_fiscal_year` assumed a
31 March year end, correct for the Indian filers and wrong for Genpact, a
December filer — so every one of its fiscal-year labels and quarter numbers
was wrong. The year end is now taken from the most common annual period
ending in the company's own data, because the data already states it and a
config field is one more thing to go stale.

**151 YTD periods reclassified, not recovered.** US 10-Q filers report
cumulative nine-month figures. Storing those beside the quarters they contain
would double-count in any aggregate, and quarterly is the finer grain — YTD is
derivable from quarters, not the reverse. So they are still skipped, but now
with their own reason and class (`YTD_NOT_STORED`), because *"we chose not
to"* and *"we could not parse it"* should never appear in a report as the same
thing.

**The `UNCLASSIFIED` bucket caught my own omission.** Adding the new YTD
rejection reason without a matching classification rule put 151 rows into
`UNCLASSIFIED` on the next run — which is exactly the failure that bucket
exists to make visible, arriving one commit after it was built.

One consequence worth watching: `INCOMPLETE_PERIOD` went from 0 findings to
25, because quarterly periods frequently carry revenue without a profit
figure. That is the check working on newly visible data, not a regression.
