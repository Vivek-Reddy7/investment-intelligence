# Phase 5 — Technology Stack

Status: **complete — all six ADRs settled; auth deferred to Phase 13**
Last updated: 2026-09-11
Depends on: [03-data-sources.md](03-data-sources.md), [02-architecture.md](02-architecture.md)

One ADR per decision. Each names the alternative it beat and the condition
under which we would revisit it. Free-tier limits are recorded with real
numbers, verified against provider documentation rather than recalled.

The roadmap promised `docs/adr/` as separate files. For a project this size
that is more ceremony than value, so the ADRs live here as numbered sections.
If the count passes roughly a dozen, split them.

---

## ADR 001 — PostgreSQL as the datastore

**Context.** The core requirement is the bitemporal query: *the latest version
of each fact where `known_from <= D`*. That is a per-group ranking query, run
across ~500 companies and tens of thousands of versioned facts, with the
result then filtered on several computed metrics.

**Decision.** PostgreSQL.

**Why it wins.** `DISTINCT ON` plus a composite index on
`(entity, period, known_from DESC)` expresses the as-of query directly and
efficiently. Window functions handle the metric computation. Declarative
partitioning is available when the fact tables grow. And the append-only
guarantee (invariant 10) can be enforced with table privileges and triggers,
so it is a database property rather than a convention someone can forget.

**Alternatives considered.**

- *SQLite.* What `paper-trader` uses. Fine there, wrong here: no concurrent
  writer while a web process reads, no role-based privilege model to enforce
  append-only, and no managed hosting story.
- *A document store.* Versioned documents are easy; the multi-metric screening
  query across the universe is not.
- *A dedicated event store / temporal database.* Correct in theory, and a
  large dependency for a feature two `WHERE` clauses can express.
- *ClickHouse or DuckDB.* Better at analytical scans, worse at the
  transactional ingestion and user data. Candidates for phase 19 alongside
  Postgres, not instead of it.

**Revisit when.** As-of queries exceed acceptable latency after indexing and
partitioning are exhausted — that is a phase 19 trigger, and the answer is
likely a columnar store beside Postgres rather than a replacement.

---

## ADR 002 — Neon as the Postgres host

**Context.** Needs to be free, real Postgres, and must not require manual
intervention to stay alive.

**Decision.** [Neon](https://neon.com) free tier.

**Verified free-tier limits (Sep 2026).**

| Limit | Value |
|---|---|
| Storage | 0.5 GB |
| Compute | 100 CU-hours / project / month |
| Autoscaling cap | 2 CU |
| Autosuspend | after 5 minutes idle, **auto-resumes on connection** |
| Projects / branches | 100 projects, 10 branches each |

**Headroom check.** Fundamentals-only for ~500 companies, ten years, quarterly
and annual, with versioning for restatements, is on the order of 1–2 million
narrow rows — well inside 0.5 GB. The Phase 3 finding that prices stay private
removed the row count that would have made storage tight.

**Why it wins over Supabase.** Supabase offers a comparable 500 MB plus
bundled Auth (50,000 MAU) and file storage, which is genuinely attractive for
phase 13. But **Supabase free projects pause after one week of inactivity and
need a manual restore from the dashboard.** That creates precisely the silent
death the architecture is built to avoid: ingestion breaks, nobody notices for
a week, the project pauses, and now recovery needs a human. Neon's 5-minute
autosuspend resumes by itself on the next connection — a latency cost, not an
availability cliff.

**Consequence.** Auth is not bundled, so ADR for authentication is deferred to
phase 13 rather than decided now.

**Revisit when.** Storage passes 70% of 0.5 GB, or compute passes 70 CU-hours
in a month. Both are phase 20 triggers with documented escapes.

---

## ADR 003 — Python for ingestion and analytics

**Context.** The primary ingestion problem is parsing company financial
statements from statutory filings — inconsistently named line items,
irregular arrival, restatements, and several document formats.

**Decision.** Python.

**Why it wins.** The parsing, normalisation and tabular-reconciliation
libraries for this work have no real equivalent elsewhere. It is also the
language the candidate already does data work in, so the learning budget goes
to the domain rather than the tooling.

**Alternatives considered.** Node/TypeScript for a single-language codebase —
rejected because filings parsing is materially harder there, and ingestion is
the part of this project most likely to be difficult. That convenience is not
worth paying for in the hardest phase.

**Revisit when.** Never, realistically. This one is load-bearing on library
ecosystem, which does not change quickly.

---

## ADR 004 — GitHub Actions cron as the scheduler

**Context.** Ingestion runs daily after market close. It must cost nothing and
its failure must be detectable.

**Decision.** GitHub Actions scheduled workflows, in a **public** repository.

**Verified facts (Sep 2026).**

- Public repositories have **unlimited** Actions minutes. Since this repo is
  public anyway, scheduling is genuinely free rather than quota-limited.
- Minimum cron interval is 5 minutes.
- **Scheduled workflows are automatically disabled after 60 days with no
  repository activity.** GitHub sends one email. Miss it and the schedule stops
  silently.
- Execution is not punctual. Delays of 10–30 minutes are common under load,
  and over an hour has been reported.
- Scheduled workflows are disabled by default in forks.

**Why this matters more than it looks.** The 60-day auto-disable is a
real-world instance of failure mode "scheduler never fires" from architecture
§4 — the one whose symptom is indistinguishable from "nothing to do". We
predicted this class of failure before choosing the tool, and the tool turns
out to have exactly it.

**Mitigation, already designed.** Expected-run gap detection over the
ingestion run log (§2.5) catches a schedule that stops firing, because a
missing run is detectable against an expected cadence. Punctuality does not
matter for a post-close daily job, so the 10–30 minute drift is irrelevant.

**Alternatives considered.** A managed cloud scheduler — costs money or ties
us to a platform this early. A self-hosted scheduler — needs an
always-on host, which is the thing we are avoiding.

**Revisit when.** Ingestion needs sub-hourly cadence, or when the run log
shows the 60-day disable has bitten more than once despite the gap alert.

---

## ADR 005 — Bitemporality in plain SQL, not a framework

**Context.** Invariants 10–12 require append-only facts, a `known_from` on
every row, and every query carrying an as-of date.

**Decision.** Model it directly: fact tables with `valid_period` and
`known_from`, no updates, no deletes, and one shared query helper that applies
the as-of predicate.

**Enforcement, not convention.** The application's database role is granted
`INSERT` and `SELECT` on fact tables and **not** `UPDATE` or `DELETE`. A
migration-only role holds schema rights. This makes invariant 10 impossible to
violate from application code rather than merely forbidden, and it doubles as
a phase 15 security control.

**Why not an ORM's versioning plugin or a temporal extension.** Both hide the
as-of predicate, which is the one piece of logic in this system that must be
obvious in every query it touches. A framework that makes it invisible makes
the central correctness property unauditable.

**Consequence.** There is exactly one code path that reads facts without an
as-of date: none. Any such path is a defect.

**Revisit when.** The hand-written as-of query appears in more than a handful
of places and starts drifting between them. The fix is a stricter single
accessor, not a framework.

---

## ADR 006 — Next.js on Vercel for the read API and web app

*Settled 2026-09-11.*

**Context.** Two surfaces: the read API (phase 10) and the web app (phase 11).
Architecture §2.8 requires the API to serve only from the store and to keep
route handlers thin over a service layer, so that extracting it into its own
service later is mechanical.

This decision is as much about career positioning as engineering, which is why
it is not mine to make.

**Option A — Next.js (React + TypeScript) on Vercel, API as route handlers.**
Fastest route to a public URL: frontend, API and deployment in one free tier.
Broadens the portfolio beyond the Angular work already on the CV. Costs a
learning curve on React during the phases where the interesting problems are
elsewhere.

**Option B — Angular front end, separate API service.**
Uses the Angular 20 skill already held professionally, so the learning budget
goes entirely into the platform. Needs two deployments instead of one, and
adds nothing new to the CV that the day job does not already show.

**Option C — Python API (FastAPI) + a separate front end.**
One language shared with ingestion, and the service boundary is real from day
one rather than extracted later. Most moving parts, most deployment surface,
slowest to a public URL.

**Decision: A.** The demonstrable thing about this project is the data
engineering and the point-in-time correctness, not the front-end framework.
Option A minimises the infrastructure between here and a public link, and the
breadth it adds is a genuine gap next to the existing Angular experience.

### Verified Hobby-plan limits (Sep 2026)

| Limit | Value |
|---|---|
| Fast Data Transfer | 100 GB / month |
| Edge requests | 1 million / month |
| Function invocations | 1 million / month *(one source says 100K — confirm against Vercel's own limits page before relying on it)* |
| Active function CPU | 4 CPU-hours / month |
| Build execution | 6,000 minutes / month |
| Deployments | 100 / day |
| Seats | 1, no collaborators |
| Cron jobs | 100 per project, but **once-per-day maximum frequency** on Hobby, fired at any point within the specified hour |

### Two constraints that came out of verifying this

**1. Commercial use is prohibited on Hobby.** Vercel describes the plan as
personal and non-commercial. A zero-revenue portfolio project is within that;
adding ads, subscriptions or any paid tier moves it to Pro at $20/month. This
is a **licensing trigger, not a capacity trigger** — it fires on a business
decision rather than a usage number, so it belongs in the phase 20 scale
document alongside the quantitative ceilings.

**2. Ingestion cannot run on Vercel, which reinforces ADR 004.** Serverless
function duration limits make a multi-hour rate-limited backfill (phase 7)
impossible there. GitHub Actions allows roughly six hours per job, which
suits it. So the split is:

- **Vercel** — web app, read API, serving only. Never ingestion.
- **GitHub Actions** — ingestion, both backfill and incremental.

That happens to match architecture §1's separation of serving from ingestion
onto different clocks, which was chosen for rate-limit reasons before either
platform was picked. The platform limits now enforce the same boundary
independently.

Vercel cron is noted as a *fallback* scheduler: its once-daily cadence is
adequate for post-close ingestion triggering, and it does not share GitHub's
60-day auto-disable failure mode. Worth remembering if ADR 004's mitigation
proves insufficient.

---

## Exit criteria for Phase 5

- [x] ADR 001 datastore
- [x] ADR 002 database host, with verified free-tier numbers
- [x] ADR 003 ingestion language
- [x] ADR 004 scheduler, with its failure mode identified and mitigated
- [x] ADR 005 bitemporal approach, with enforcement mechanism
- [x] ADR 006 web stack — Next.js on Vercel, with verified Hobby limits
- [ ] Auth ADR — deliberately deferred to phase 13
- [x] Phase 6 may begin
