# Project analysis: novelty, scope, stress test, roadmap

A structured, adversarial review of this project against the **project-architect**
protocol (four phases, then a summary). It is written to find what is wrong,
not to sell the project. Where a claim could not be verified it says so.

Status date: 2026-10-09. Figures about the project (10 tracked companies, 9 with
data, 31 migrations) are those in the [README](../README.md).

---

## Phase 1 — Landscape & Novelty Analysis

### 1A. The nearest neighbors

| Neighbor | What it is | Key strength | Primary limitation |
|---|---|---|---|
| [S&P Compustat Point-in-Time](https://www.spglobal.com/market-intelligence/en/solutions/products/fundamental-data) | Commercial database preserving each restatement; S&P's own pages put its snapshots as far back as 1987 | Institutional depth, breadth, decades of history | Licensed, closed data and schema: it cannot be audited or run by an individual |
| [Sharadar Core US Fundamentals (SF1)](https://data.nasdaq.com/databases/SF1) | Paid dataset on Nasdaq Data Link with as-reported dimensions keyed to the filing date | Clean as-reported series | A purchased dataset, not an inspectable pipeline; US companies only |
| [pit-fundamentals](https://github.com/christianpichichero-max/pit-fundamentals) | Small CC0 CSV of US fundamentals from SEC EDGAR, each value stamped with a `first_filed` date, restatements kept with a flag | **Closest in spirit:** open, same source, same point-in-time idea | A static proof pack. Its README states 5 companies and 225 rows, annual filings only; broader data sits behind a paid API. No store, no screening layer, no integrity enforcement |
| [edgartools](https://github.com/dgunning/edgartools) | MIT-licensed Python library for reading EDGAR filings and XBRL | Excellent parsing of the same source | Reads filings; it is not a versioned store with a screening layer |
| Free Indian screeners (Screener.in and similar) | Web screeners over current financials | Broad Indian coverage, polished, free | Show the current, restated view, so "what did this screen return on a past date?" cannot be asked |

The space is **not emerging**. The idea has commercial incumbents and at least
one open, free neighbor that does the core stamping already. That is the main
fact of this phase.

### 1B. The delta

**What this does that none of the neighbors do, as claimed:**

- It keeps the history in a **database that refuses to lose it**: append-only
  enforced by triggers *and* role privileges, with `facts_as_of(when)` as the
  only read path. The neighbors keep history by convention or by contract.
- It adds a **screening layer** with provenance: every number links to the
  filing it came from.
- It documents its own reasoning (phase documents written before the code).

**What it does not do that a neighbor does:** cover more than ten companies,
carry a data licence, or match Indian Ind AS figures. On coverage and history
every commercial neighbor wins outright.

**Novelty rating: Low.** The point-in-time idea is decades old, commercially
sold, and already published openly on EDGAR data. The differentiators
(database-enforced invariants, provenance, written decisions) are real
engineering quality, but they are quality of implementation, not a new
capability. A rating of **Medium** would require the one thing that is
underserved and not yet delivered: point-in-time data for **Indian** filings.

**Pivots that could establish novelty:**

1. **Narrow to Indian point-in-time fundamentals.** Ind AS filings with
   original-and-restated versions are the unserved sub-problem. This is also the
   project's own largest open problem ([docs/03 §6](03-data-sources.md)).
2. **Publish a measurement, not a product.** "How often does the restated view
   change a screen?" on real filings. The tool then becomes the instrument, and
   the result is the contribution.
3. **Extract the integrity layer.** The append-only/as-of enforcement and its
   test suite as a small reusable Postgres module, independent of the data source.

---

## Phase 2 — Strategic Positioning & Scope

### 2A. Build vs. contribute matrix

| Option | Rationale | Recommended? |
|---|---|---|
| Fork / extend an existing OSS project | The only open neighbor is a CSV pack with no schema to extend; forking it would discard the database-enforced design | **No** |
| Contribute upstream to an existing project | The point-in-time store is outside edgartools' scope (a parser). A narrow upstream contribution, such as documenting how to read restatement history, is possible but small | **Partially**: as a side effort, not the plan |
| Build a new standalone OSS project | Already done. Justified by the enforcement design and the Indian angle; weak on the concept alone | **Yes**, with the Phase 1B caveat |
| Build a closed-source / enterprise tool | No data licence, ten companies, no moat, one maintainer; nothing to sell | **No** |
| Academic research only (no implementation) | The contribution would be an empirical restatement study, which needs more data than the project has today | **No, not yet**: revisit after Stage 2 below |

**Recommendation:** keep it a standalone OSS project, and treat the next gain
as evidence and coverage, not more platform. Gap size is small, community
maturity of neighbors is high, and maintenance cost lands on one person, so
every new feature should be weighed against that.

### 2B. Output scope recommendation

**Blog post + reference implementation.** The repository is the reference
implementation. What is missing is the written result that gives it a reason to
be read: a measured finding (Stage 2) about how the as-of view differs from the
restated view. A paper is not warranted at **Low** novelty and nine companies;
a library is premature because the reusable part (Pivot 3) is not yet separated.

---

## Phase 3 — Relevance & Critical Stress Test

### 3A. Relevance check

- **Why it matters:** lookahead bias is a well-known way for a backtest or screen to look better than it was. Free tools that overwrite restated figures cannot answer past-date questions. This is the problem the project addresses.
- **Verification note:** a headline statistic for how often companies restate (S&P's and an SSRN paper's figures) was **not** verified. Both pages returned 403. No such number is cited here, and none should be added to the README without checking the source.
- **Relevance threshold:** **met narrowly, and only for a specific audience.** As an engineering demonstration of data-integrity design, relevance is high. As a product for Indian retail investors it is **niche and weak today**: the figures it serves will not match the screeners they already use, and it covers about ten companies. Relevance rises only with Indian filings coverage (Pivot 1) or a demonstrated effect (Stage 2).

### 3B. The pros

- **The invariant is enforced twice.** Triggers cover the table owner; privileges cover a dropped trigger. The failure it prevents (one `UPDATE` that destroys history irrecoverably) is exactly the one that happens in practice.
- **The point-in-time axis is not inferred.** It comes from the SEC's own `filed` dates on `companyfacts`, so the as-of view is driven by a recorded fact rather than an estimate.
- **There is a real two-document restatement to show.** Sify Technologies' total equity moved from ₹17,145,688,000 (filed 2023-06-28) to ₹15,145,688,000 (a 20-F/A filed 2025-01-13); both are held and linked ([docs/07](07-first-source.md)).
- **Licensing was researched, and it changed the product.** Prices are local-only by design, encoded as `redistributable = false`, rather than discovered after launch.
- **The documentation was verified by execution.** Phase 18 wiped the environment and followed the README verbatim, and the run found real defects ([docs/18](18-documentation.md)).

### 3C. Harsh criticisms and limitation study

**Technical**

- **The line-item vocabulary is a guess.** The data model doc itself says 24 codes were chosen for the v1 metric set and that real filings will not all map onto them. Silent misclassification of an XBRL tag produces a wrong number with a valid provenance link.
- **Append-only cuts both ways.** A bad ingest cannot be deleted; it can only be superseded by a later version, and every reader must be correct about which one wins. The design buys integrity at the cost of permanent mistakes in history.
- **As-of resolution is a calendar date.** The SEC `filed` field is a date, not a timestamp; same-day ordering is not representable.

**Theoretical**

- **Point-in-time removes one bias, not all of them.** Survivorship is only partly handled. The tracked set is **seeded from today's NIFTY 500** ([docs/02](02-architecture.md)). A company that delisted before seeding is not in the store at all, so "a delisting is a date, not a deletion" is true only for companies that were tracked while listed. The architecture document calls the result free of survivorship bias "in the way that matters"; that overstates it.
- **`filed` is not when the market knew.** Foreign private issuers typically publish results in earnings releases before the annual 20-F. The as-of view therefore says "knowable by formal filing", which is conservative, and is not "what the market knew".
- **Nine companies cannot support any statistical claim.** The research layer's information-coefficient study is a pipeline test, not evidence of an edge.

**Practical**

- **It will not match the numbers Indian users know.** Figures are as filed under IFRS or US-GAAP, not Ind AS.
- **Not deployed, one maintainer, email unwired.** Sign-in links and alerts are built but cannot be delivered. The nightly workflow exits with a notice until a `DATABASE_URL` secret exists, so a green run currently means "skipped".
- **The data problem that matters is unsolved.** No route to Indian filings with version history is confirmed ([docs/03 §6](03-data-sources.md)).

**Competitive**

- **No moat.** A well-funded team, or an incumbent, could rebuild this quickly, and an open CC0 neighbor already stamps filing dates. The value is the implementation and the written decisions, which is a reputation asset, not a defensible one.

**Limitation study: concrete edge cases and failure modes**

1. **A tracked company with no data.** ICICI Bank returns 404 from `companyfacts` and is left in the tracked set on purpose. In any screen it is silently absent unless the gap report is read.
2. **A restatement outside the lookback.** The incremental job re-examines two years ([docs/08](08-incremental-ingestion.md)); the Sify restatement arrived about eighteen months late. A revision to an older period is outside that window and would be found only by a full backfill.
3. **Mixed reporting currencies.** Sify reports in INR while the README's example shows INFY in USD. Ratio screens are safe; any screen on absolute values across companies is not, without explicit currency handling.
4. **Restatement-sparse history.** A company that never restates gives the machinery nothing to show. With one real documented case, the headline benefit is demonstrated by a single example.
5. **Source dependence.** The whole store rests on one API's schema and politeness limits. A change in `companyfacts` breaks ingestion for every company at once.

---

## Phase 4 — Structural Breakdown & Execution Roadmap

### 4A. Approach selection

**Bottom-up**, which is what was done and what the remaining risk still calls
for. The core primitive (the versioned store with as-of reads) is built and
tested. The research risk is now **data**, not integration, so the next work
should validate the smallest data-risk first (can Indian filings be obtained
with versions?) before widening the platform. No deadline was given, so stages
are sized relatively and not pinned to dates.

### 4B. The action map

| Stage | Description | Key deliverable | Effort | Dependencies |
|---|---|---|---|---|
| **1. Deploy (Phase 12)** | Neon project, Vercel app, GitHub Actions cron, uptime monitor | A public URL and a nightly ingest that really ingests | Days | Accounts and credentials created by the owner; the `ii_web` role and secrets set by hand |
| **2. Measure the effect** | Quantify how often as-of-original differs from latest-restated across the stored history, and whether it changes any screen | A short written result with the counts, and a decision on README framing | Days to a week | Stored history only; no deployment needed |
| **3. Close the universe gap** | Decide how to represent companies that delisted before the tracked set was seeded, or state the limit in the product | Either a method or an explicit, visible limitation | Weeks | Stage 2 (so the effort is sized by the measured effect) |
| **4. Indian filings spike** | Time-boxed attempt to get Ind AS filings with original filing dates and restatements | A yes/no feasibility note with one company end to end | Weeks to months | Licence check on the source; Stage 2 shows whether the effort is justified |

### 4C. Stage-gate questions

1. **Deploy:** After one scheduled run, does the status page distinguish "ingested" from "skipped", and does it complete on the free-tier pooled and direct connections without manual intervention?
2. **Measure the effect:** On the stored history, in what fraction of (company, line item, period) groups do the original and latest filed values differ by more than 1%? If it is effectively zero, the headline claim has no demonstrated effect on this universe and the README framing must change.
3. **Close the universe gap:** Can any available source produce delisting or listing dates for companies *not* in the tracked set, without a licence that blocks reuse? If not, the limitation goes on the screen itself.
4. **Indian filings spike:** For one company, can two filings for the same period be retrieved from a free, reusable source with distinct filing dates and different values? One pair is enough to proceed; none ends the pivot.

---

## TL;DR

> - **Novelty: Low.** Point-in-time fundamentals are sold commercially and an open CC0 neighbor already stamps filing dates; the differentiators are implementation quality, not a new capability.
> - **Recommended scope:** blog post + reference implementation, centred on a measured result, not a paper or a product.
> - **Top criticism 1 — no moat and little coverage:** ten tracked companies, no data licence, and a rebuildable idea.
> - **Top criticism 2 — only part of the bias is removed:** the universe is seeded from today's index, and the as-of date is a formal filing date, not when the market knew.
> - **Approach:** bottom-up. The core primitive is proven; validate the data risk (Indian filings with version history) before widening the platform.
> - **First next step:** Stage 2, measure how often the as-of view differs from the restated view on the stored history, because the project's headline claim currently rests on one documented case.
