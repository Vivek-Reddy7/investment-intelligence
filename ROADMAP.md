# Roadmap

Fourteen phases. Each has one objective and a written exit criterion. A phase
is done when its exit criterion is demonstrably met, not when it feels close.
We do not start a phase before the one it depends on has exited.

Rule: **no feature work happens outside a phase.** If something interesting
turns up mid-phase, it goes in `docs/backlog.md` and we carry on.

| # | Phase | Objective | Exits when | Status |
|---|---|---|---|---|
| 1 | Vision and scope | Fix what we are building and what we are not | Scope signed off, open decisions answered | **Done** (name pending) |
| 2 | System architecture | Component boundaries, data flow, failure modes | Architecture doc + diagram agreed; every v1 feature has a home | **In review** |
| 3 | Technology stack | Choose languages, frameworks, datastores, hosting | Each choice written as an ADR with the alternative it beat | Blocked on 2 |
| 4 | Database and data models | Schema for instruments, prices, fundamentals, users | Migrations run; models enforce their own invariants | Blocked on 3 |
| 5 | Market data sources | Identify sources and verify their terms in writing | Per-source note: coverage, limits, licence, redistribution rights | Blocked on 1 |
| 6 | Backend and ingestion | Scheduled ingestion, storage, internal API | Daily ingest runs unattended; gaps are visible, not silent | Blocked on 4, 5 |
| 7 | Frontend and dashboard | Company pages, charts, screening UI | A stranger can screen and read a company page unaided | Blocked on 6 |
| 8 | Analytics and screening | Derived metrics, filters, peer comparison | Every metric traceable to its inputs and ingestion date | Blocked on 6 |
| 9 | Accounts and personalisation | Auth, watchlists, portfolio, alerts | A user can sign in, save a watchlist, receive an alert | Blocked on 7 |
| 10 | Testing, logging, monitoring | Confidence that breakage is detected | Ingestion failures page us; core paths covered by tests | Runs alongside 6-9 |
| 11 | Deployment on free tier | Public URL, scheduled jobs, zero cost | Reachable by a stranger; running cost is zero | Blocked on 7 |
| 12 | Scale path | Free-tier ceilings identified and escape routes documented | Each limit has a named trigger and a config-level fix | Blocked on 11 |
| 13 | Documentation | Architecture, runbook, contribution flow | A stranger can run it locally from the README alone | Continuous |
| 14 | Iterate toward production | Expand deliberately from the deferred list | Ongoing | — |

## Dependency notes

Phase 5 only depends on Phase 1, so market-data research can run in parallel
with architecture and stack work. It is also the phase most likely to force a
rethink, because a source we assumed we could use may turn out to be off
limits. Worth starting early for that reason.

Phase 10 is not a phase we arrive at. Tests and logging are written with the
code in 6 through 9. It is listed separately so it gets an explicit exit
criterion rather than being assumed.

Phase 13 is continuous for the same reason, with a checkpoint at the end.

## Phase log

| Date | Phase | Event |
|---|---|---|
| 2026-09-11 | 1 | Opened. Draft vision and scope written, awaiting sign-off. |
| 2026-09-11 | 1 | Decisions settled: NSE equities only (8.1), fundamentals in v1 (8.2), public read without account (8.3). Name still open — blocks publication, not Phase 2. Phase 1 closed. |
| 2026-09-11 | 2 | Opened. Architecture drafted: 11 components, 9 invariants, 10 failure modes. No technology named — that is Phase 3. |
