# Roadmap

Twenty-two phases in four tiers, from idea to a scalable production system.

Each phase has one objective and a written exit criterion. A phase is done
when its exit criterion is demonstrably met, not when it feels close. We do
not start a phase before its dependencies have exited.

**Rule: no work happens outside a phase.** If something interesting turns up
mid-phase, it goes in [`docs/backlog.md`](docs/backlog.md) and we carry on.

## Tiers at a glance

| Tier | Phases | Milestone | Cost |
|---|---|---|---|
| **0 · Definition** | 1–5 | Nothing is built. Everything is decided. | — |
| **1 · MVP on free tier** | 6–12 | **Public URL a stranger can use** | ₹0 |
| **2 · Production readiness** | 13–18 | **Safe to leave running unattended** | ₹0 |
| **3 · Scale and advanced** | 19–22 | Survives growth past free-tier limits | Paid, deliberately |

## Three changes from the original ordering

**1. Data sources moved from phase 5 to phase 3** — ahead of architecture and
stack. The product wedge (point-in-time screening, decision 8.5) depends
entirely on what historical data we can legally obtain. If as-reported history
and historical index constituents are unavailable, the wedge degrades and the
architecture changes. Choosing a technology stack before knowing this is
choosing in the dark. It is also the phase most likely to invalidate earlier
decisions, which is a reason to do it early, not late.

**2. Ingestion split into backfill and incremental** (phases 7 and 8). These
look like one problem and are not. Backfill loads years of history under a
rate limit and runs for hours; incremental loads one day and runs in seconds.
Different failure modes, different retry logic, different code.

**3. Security promoted to its own phase** (15), and **data quality** to its
own phase (17). Both were implicit. Given a bitemporal store whose entire
value is that its history is trustworthy, silent data corruption is the
worst thing that can happen to this product, and it deserves a gate.

---

# TIER 0 — DEFINITION

*No code is written in this tier. The output is decisions and documents.*

### Phase 1 — Vision and scope · **DONE**
**Build:** The problem statement, the users, the v1 boundary, and the hard
non-goals.
**Why:** The non-goals are the constraints that shape the architecture. No
recommendations (SEBI RA regulations), no redistribution of licensed broker
data (exchange data vending policy), no live ticks, no order placement.
Discovering these in phase 11 would mean rebuilding.
**Deliverable:** [`docs/01-vision-and-scope.md`](docs/01-vision-and-scope.md)
**Exit:** Scope signed off. Decisions 8.1–8.3, 8.5, 8.6 settled. *(Name still
pending — blocks publication only.)*

### Phase 2 — Requirements and product wedge · **DONE**
**Build:** The answer to "why would anyone use this instead of Screener.in".
**Why:** Current-fundamentals screening is solved and free in India. Without a
wedge this is a portfolio piece, not a product. The wedge chosen —
point-in-time correctness — is the one requirement that cannot be retrofitted,
because history that was overwritten is gone.
**Deliverable:** [`docs/01-vision-and-scope.md`](docs/01-vision-and-scope.md)
§2.1; architecture §2.12; invariants 10–12.
**Exit:** Wedge and universe settled. Consequences propagated to architecture.

### Phase 3 — Data sources and legal verification · **GO DECISION REACHED**
**Build:** A written per-source assessment: coverage, history depth, rate
limits, cost, licence terms, redistribution rights, and — critically —
whether as-reported (unrestated) history and historical index constituents
are obtainable.
**Why:** This is the highest-risk phase in the project. It can invalidate the
wedge, the universe decision, and parts of the architecture. Everything
downstream is built on assumptions this phase either confirms or destroys.
Doing it after the stack is chosen means choosing blind.
**Deliverable:** `docs/03-data-sources.md` — one section per candidate source,
each with a verdict and a link to the terms actually read. Plus a go/no-go on
point-in-time correctness with as-reported history, versus forward-only from
our first ingestion.
**Exit:** Every source we intend to use has verified terms in writing. No
source is adopted on recollection. A primary and a fallback are identified for
prices, fundamentals, corporate actions, and index membership.

### Phase 4 — System architecture · **DONE**
**Build:** Component boundaries, data flow, invariants, failure modes.
**Why:** Separating ingestion, computation and serving onto different clocks
is what makes this survivable on free infrastructure. If a page load could
trigger a provider fetch, one popular link exhausts a rate limit and takes the
site down.
**Deliverable:** [`docs/02-architecture.md`](docs/02-architecture.md) — 11
components, 12 invariants, 10 failure modes, no technology named.
**Exit:** Invariants agreed (they become review rules). Every v1 feature has a
home. Revised in §2.13 for the Phase 3 licensing finding — component set
unchanged, roles inverted: fundamentals become the primary ingester, prices
move to the private path.

### Phase 5 — Technology stack · **DONE**
**Build:** Language, framework, datastore, scheduler, host, frontend
framework — each chosen deliberately.
**Why:** Free-tier constraints and bitemporal querying narrow the field more
than usual. A datastore that cannot express "latest version as of date X"
efficiently makes the core feature slow.
**Deliverable:** [`docs/05-technology-stack.md`](docs/05-technology-stack.md) —
six ADRs, each naming the alternative it beat and the revisit condition.
**Exit:** Every choice is an ADR, not a preference. Free-tier limits of each
chosen service are written down with numbers.

---

# TIER 1 — MVP ON FREE TIER

*Goal: a public URL a stranger can use, costing nothing to run.*

### Phase 6 — Data model and schema · **DONE**
**Build:** Bitemporal schema for instruments, index membership, prices,
corporate actions, fundamentals, and the ingestion run log.
**Why:** This is where invariant 10 gets enforced. One `UPDATE` written by a
future version of us destroys history irrecoverably, so append-only must be a
database constraint rather than a convention.
**Deliverable:** Migrations, model definitions, and a schema document
explaining each time axis.
**Exit:** Append-only enforced at the database level. A point-in-time query
returns correct results against hand-seeded test data including a restatement
and a delisting.

### Phase 7 — Ingestion: backfill · **DONE for stage one**
**Build:** Bulk historical load for the NIFTY 500 universe under provider rate
limits, resumable after interruption.
**Why:** Backfill is a different problem from the daily job: it runs for
hours, it will be interrupted, and re-running it from scratch is unacceptable.
It is also the first real test of whether the schema holds.
**Deliverable:** A backfill command with checkpointing and a progress report.
**Exit:** Full universe loaded with target history depth. Re-running is a
no-op. Gaps are enumerated, not hidden.

### Phase 8 — Ingestion: incremental and scheduled · **DONE** (schedule activates at deploy)
**Build:** The daily post-close job, plus event-shaped fundamentals ingestion
for newly filed and restated statements.
**Why:** This is the part that runs forever unattended. Its correctness
property is that re-ingesting an unchanged value creates no new version, while
a genuinely changed value does.
**Deliverable:** Scheduled workers, dedup on natural key plus value, run-log
rows for every attempt including failures.
**Exit:** Runs unattended for a week. A failed run is visible as a row. A
simulated restatement produces a new version, not an overwrite.

### Phase 9 — Analytics and screening engine · **DONE**
**Build:** Derived metrics with provenance, multi-criteria screening, peer
comparison, and as-of-date screening.
**Why:** This is the product. Everything before it is plumbing.
**Deliverable:** Metric definitions, the screening query layer, materialised
current view plus on-demand historical computation.
**Exit:** Every metric names its inputs, period and computation time. A screen
run as of a past date excludes companies that had not yet listed and uses
figures as they were then reported.

### Phase 10 — Read API · **DONE**
**Build:** The query surface: one instrument over time, many instruments
filtered, reference data. Freshness stamps from the run log.
**Why:** It enforces invariant 9 — no serving path triggers an external fetch
— and it is the contract the frontend is built against.
**Deliverable:** A documented API with stable response shapes.
**Exit:** Serves entirely from the store. Every response carries data
freshness. Cold start tolerated.

### Phase 11 — Frontend and dashboard · **DONE** (charts deferred)
**Build:** Company pages, price and financial history charts, the screener UI,
peer comparison, and the as-of-date control.
**Why:** Until this exists, nothing is demonstrable. The as-of-date control is
where the wedge becomes visible to a user rather than a property of the store.
**Deliverable:** A responsive web app.
**Exit:** A stranger can screen the universe and read a company page without
being told how, and can see where every number came from.

### Phase 12 — Free-tier deployment · **IN PROGRESS** · **MVP MILESTONE**
*Design, connection topology and setup script done —
[docs/12-deployment.md](docs/12-deployment.md). What remains needs a Neon
project and a Vercel account, which are the only parts of this project that
cannot be written down here.*
**Build:** Public hosting, scheduled job execution, managed database, domain.
**Why:** A project that only runs locally is not a product, and free-tier
deployment has its own failure modes — sleeping databases, schedulers that
silently do not fire, cold starts.
**Deliverable:** A public URL and a deployment runbook.
**Exit:** Reachable by a stranger. Scheduled ingestion runs in the deployed
environment. Monthly cost is zero. Scheduler-did-not-fire is detected.

---

# TIER 2 — PRODUCTION READINESS

*Goal: safe to leave running unattended and to put on a CV.*

### Phase 13 — Accounts and personalisation · **DONE** (email sender needs Phase 12)
**Build:** Email authentication, watchlists, manually entered portfolio,
saved screens, price and metric alerts.
**Why:** Moved out of MVP deliberately — the public read surface is
demonstrable without login, and auth is where security mistakes live. Better
built once the data layer is proven.
**Deliverable:** Auth flow, user domain schema, alert evaluator.
**Exit:** A user can sign in, save a screen, and receive an alert. Market data
still has no dependency on the user domain (invariant 8).

### Phase 14 — Testing strategy and coverage · **DONE**
**Build:** Unit tests on metric computation, contract tests on source
adapters, integration tests on ingestion idempotency and point-in-time
queries, end-to-end on the screening path.
**Why:** Tests are written continuously from phase 6 onward. This phase is the
gate where we prove coverage of the things whose failure is *invisible* —
lookahead leaking into a screen, a restatement silently overwriting.
**Deliverable:** A test suite in CI, plus a written note on what is
deliberately untested and why.
**Exit:** The invisible-failure cases have named tests. CI blocks merge on red.

### Phase 15 — Security hardening · **DONE**
**Build:** Secret management, authentication hardening, rate limiting, input
validation, dependency scanning, least-privilege database roles.
**Why:** A public site with accounts is a target regardless of size. The
append-only guarantee is also a security property: the database role the
application uses must be incapable of `UPDATE` or `DELETE` on fact tables.
**Deliverable:** A threat model for the actual attack surface and the
mitigations in place.
**Exit:** No secrets in the repository. Application role cannot mutate
history. Dependency scanning in CI.

### Phase 16 — Observability · **DONE** (sending needs Phase 12)
**Build:** Structured logging, ingestion metrics, data-freshness monitoring,
alerting on failure, and an internal status page.
**Why:** The failure that matters here is silent: data stops updating and the
site keeps serving stale numbers confidently. Freshness monitoring is the
specific defence.
**Deliverable:** Dashboards and alert rules, on free tooling.
**Exit:** A deliberately broken ingestion run produces an alert within one
cycle. Staleness is visible to users, not only to us.

### Phase 17 — Data quality and reconciliation · **DONE** (discontinuity detection deferred)
**Build:** Cross-source validation, corporate-action discontinuity detection,
fundamentals sanity rules, and a reconciliation report.
**Why:** For a product whose entire claim is that its history is trustworthy,
silent corruption is the worst possible failure. A missed split makes price
history permanently wrong and nothing looks broken.
**Deliverable:** Automated quality checks in the pipeline and a visible data
quality report.
**Exit:** A seeded corruption is caught by the checks rather than by a user.

### Phase 18 — Documentation and runbooks · **DONE — PRODUCTION MILESTONE**
**Build:** Architecture docs kept current, a local setup guide, operational
runbooks, and an API reference.
**Why:** Documentation has been continuous since phase 1; this is the
checkpoint where it is verified rather than assumed.
**Deliverable:** A README a stranger can follow to a running local instance.
**Exit:** Someone who has never seen the project runs it locally using only
the docs.

---

# TIER 3 — SCALE AND ADVANCED

*Goal: outgrow the free tier deliberately rather than by surprise.*

### Phase 19 — Performance and cost engineering
**Build:** Query profiling, indexing strategy, caching, and cost attribution.
**Why:** Historical as-of queries are the expensive path by design. Making
them acceptable without materialising every date is the interesting problem.
**Deliverable:** Benchmarks with numbers, before and after.
**Exit:** Live screening is fast. Historical screening is tolerable. Both have
measured, not estimated, numbers.

### Phase 20 — Scale path execution
**Build:** The documented escape routes from each free-tier ceiling — storage,
compute, connections, scheduled job minutes, bandwidth.
**Why:** The scale path is only real if each limit has a named trigger and a
change that is configuration rather than a rewrite. Untested escape routes are
assumptions.
**Deliverable:** A scaling document with a trigger, an action and a cost per
limit — and at least one route actually rehearsed.
**Exit:** Each ceiling has a number, a trigger and a tested action.

### Phase 21 — Private broker path
**Build:** Per-user broker connection: credentials held per user, data fetched
with them, served only to that user, never written to the core store.
**Why:** It is the only legal way to offer live and personal portfolio data.
It was fenced in the architecture from day one precisely so it could be built
without contaminating the public store.
**Deliverable:** Per-user integration with structurally enforced isolation —
no write handle to the core store at all.
**Exit:** Invariant 7 holds under test. A user's broker data is unreachable by
any other user.

### Phase 22 — Advanced intelligence and expansion
**Build:** From the deferred list, with evidence: sector analytics, factor
exposures, screen backtesting via `paper-trader` as a library, additional
markets, filings and events.
**Why:** Expansion happens after the foundation is trustworthy, and each item
is justified by observed use rather than by the roadmap having a gap.
**Deliverable:** Per-feature, decided one at a time.
**Exit:** Ongoing.

**2026-09-22 — started out of order, recorded rather than hidden.** The rule
at the top of this document is "no work happens outside a phase," and phases
run in sequence; Phase 12 has not exited. A factor score (migration 024,
`analytics/factor_score.py`) was built anyway, on explicit request, against a
deadline outside this project. It is exactly the "factor exposures" item
already listed above, just started early rather than invented outside scope.

What shipped and what did not, so the deviation is bounded rather than open:
daily price bars and a rank-combined factor score across momentum, margin
and revenue growth, all point-in-time correct, with real tests. **Not
shipped:** it is not wired into the public web app or API. Building it
surfaced a live licensing question — Yahoo's terms restrict redistribution
of `yfinance`-sourced data for a public, customer-facing product, a
materially different risk than `paper-trader`'s private local use — recorded
in [docs/03-data-sources.md §7](docs/03-data-sources.md). That restriction, not a
technical gap, is why this stays CLI-only until a source with terms that
actually permit public display replaces it, matching the bar SEC_EDGAR
already met before Phase 3 cleared fundamentals for public use.

---

## Dependency notes

Phase 3 only depends on phase 1, and it is the highest-risk phase. Starting it
early is the single biggest de-risking move available.

Phases 14, 16 and 18 describe work that happens continuously from phase 6
onward. They are listed as phases so that they get explicit exit criteria
rather than being assumed done.

Phase 21 is buildable much earlier in principle. It sits in tier 3 because it
introduces credential handling, which should not be attempted before phase 15.

## Phase log

| Date | Phase | Event |
|---|---|---|
| 2026-09-11 | 1 | Opened. Draft vision and scope written. |
| 2026-09-11 | 1 | Settled: NSE equities (8.1), fundamentals in v1 (8.2), public read (8.3). Closed. |
| 2026-09-11 | — | Architecture drafted: 11 components, 9 invariants, 10 failure modes. |
| 2026-09-11 | 2 | Wedge settled (8.5): point-in-time screening. Universe (8.6): NIFTY 500. |
| 2026-09-11 | — | Architecture revised for bitemporality (§2.12). Invariants now 12. |
| 2026-09-11 | — | Roadmap expanded to 22 phases in 4 tiers. Data sources moved to phase 3; ingestion split; security and data quality promoted to phases. |
| 2026-09-11 | 3 | NSE tariffs verified: public display of EOD market data ₹1,10,000 per medium, 15-min delayed ₹60,000, no delay makes it free. |
| 2026-09-11 | 3 | NSE data policy read in full. Non-commercial waiver requires a confidentiality declaration — closed. Clause 7.4 bans simulation/virtual trading outright, at any price. |
| 2026-09-11 | 3 | **GO decision:** fundamentals public (statutory filings, §4), prices private (per-user broker). Architecture unchanged. Scope narrowed: no public valuation ratios. |
| 2026-09-11 | 4 | Revised (§2.13). Fundamentals worker becomes primary; price worker and price adjustment move to the private path; universe membership derived from our own filing data since historical index constituents are licensed. Invariants unchanged. |
| 2026-09-11 | 5 | Opened. ADRs 001-005 settled: Postgres, Neon, Python ingestion, GitHub Actions cron, bitemporality in plain SQL with privileges enforcing append-only. ADR 006 (web stack) open. |
| 2026-09-11 | 18 | **Docs verified by running them.** Wiped venv, node_modules, both databases and the web role, then followed the README verbatim — it worked, and found a missing `ii_web` role step, a stale-dev-server trap, and a wrong Next workspace root. Makefile so docs and commands cannot drift. 40 doc-rot tests. **Tier 2 complete.** |
| 2026-09-11 | 16 | **Observability.** Structured JSON logging with run correlation and key-name redaction — the Phase 6 item architecture asked for and I skipped. Edge-triggered operational alerts that resolve. Public /status page and `cli status`. Exit criterion demonstrated: broken run produces a CRITICAL alert in one cycle, 4 further cycles produce none. 319 tests. |
| 2026-09-11 | 15 | **Security.** Threat model, rate limiting on login flooding and the historical-screen DoS, exhaustive privilege audit, security headers. `npm audit` found a **critical Next.js RCE** (GHSA-9qr9-h5gf-34mp) — fixed by a patch bump. Removed a committed password and a `.env` example the gitignore had hidden. 293 tests. |
| 2026-09-11 | 13 | **Accounts.** Magic-link auth with no passwords anywhere (ADR 007), user data in its own schema with row level security, edge-triggered alerts. 258 tests. Found that the isolation tests proved nothing -- superusers bypass RLS and the harness runs as one. |
| 2026-09-11 | 7 | **215 quarterly facts recovered** from the Phase 17 rejection classification. Quarter now derived from period end rather than EDGAR's unreliable `fp`; fiscal calendar learned per company, fixing Genpact's December year end. Store went from 0 to 551 non-annual facts. Real failures down from 372 to 6. 216 tests. |
| 2026-09-11 | 17 | **Data quality.** Six accounting-identity checks; cross-currency validation gives cross-source-style checking from a single source (43 pairs, 0 inconsistencies). Coverage reported beside findings so an inert check cannot read as clean data. The 372 rejections classified: 366 come from 2 companies and are one parser gap, now diagnosed precisely in the backlog. 202 tests. |
| 2026-09-11 | 14 | **179 tests, 91% coverage, CI gating merges.** Real deliverable is the 24-row invisible-failure inventory in 14-testing-strategy.md. Adapter contract suite added — found that FixtureSource had diverged from EdgarSource, so engine tests were validating behaviour production lacks. Also fixed `gaps()` reporting 'no gaps' when nothing was planned. |
| 2026-09-11 | 8 | **Daily job built.** 8s over 10 companies, 0 writes when nothing changed. ingestion_health() detects the run that did NOT happen; /api/v1/health returns 503 out-of-band because a scheduler cannot detect its own failure to run. Migration 016 splits NEVER_RAN from NEVER_SUCCEEDED. 134 tests. |
| 2026-09-11 | 10-11 | **API and UI running locally.** 4 versioned routes, screener with as-of control, provenance to the SEC document, freshness on every response. Read-only enforced by the ii_web database role. 40 contract checks. |
| 2026-09-11 | 9 | **Screening works.** 9 metrics, point-in-time screens, provenance to the SEC document. 118 tests. Migration 014 fixes a fail-closed universe bug: every screen returned zero because universe_as_of required lifecycle events the adapter never ingests. |
| 2026-09-11 | 7 | **SEC EDGAR loaded.** 2,393 facts, 118 filings, 9/10 companies, re-run a verified no-op. Natively bitemporal, explicitly reusable. Wedge verified on real data incl. a Rs 2bn Sify restatement across two filings. Four invisible bugs found by live data; see 07-first-source.md §4. |
| 2026-09-11 | 3 | Commercial fundamentals APIs priced and terms read (03a). No off-the-shelf plan grants public display. Separately, none serve as-reported figures — so paying buys a conventional screener, not the wedge. |
| 2026-09-11 | 7 | Backfill engine built: adapter interface, boundary validation, idempotent versioned writer, resumable checkpointed backfill. 69 tests. **Blocked on a source**: official BSE/NSE filings APIs are ~Rs 20 lakh/year, unofficial scrapers are excluded by instruction. See 03-data-sources.md §6. |
| 2026-09-11 | 6 | Schema built: 9 forward-only migrations, 46 tests. Append-only enforced by triggers and privileges. **Found and closed a TRUNCATE hole** — BEFORE DELETE triggers do not catch TRUNCATE, so the table could be emptied with enforcement in place, silently. |
| 2026-09-11 | 5 | ADR 006 settled: Next.js on Vercel. Verified Hobby limits; commercial use prohibited (a licensing trigger, not a capacity one). Ingestion cannot run on Vercel due to function duration limits, which independently enforces the serving/ingestion split from architecture §1. **Tier 0 complete.** |
