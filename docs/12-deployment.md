# Phase 12 — Free-tier deployment

Status: **in progress**
Last updated: 2026-09-21

The exit criteria, from [ROADMAP.md](../ROADMAP.md):

> Reachable by a stranger. Scheduled ingestion runs in the deployed
> environment. Monthly cost is zero. Scheduler-did-not-fire is detected.

This phase is last in tier 1 and was left last deliberately. It is the only
phase that needs accounts and credentials rather than code, so everything
buildable without them was built first. Nothing here changes application
logic — if it does, something earlier was wrong.

---

## 1. Why free-tier deployment has its own failure modes

A paid environment fails loudly: the bill arrives, the alert fires, someone is
on call. A free one fails quietly, and quietly is the failure mode this whole
project is built against.

The three that matter here:

1. **The database sleeps.** Neon suspends after 5 minutes idle. It resumes on
   connection, so this is latency, not death — but only because
   [ADR 002](05-technology-stack.md) rejected Supabase, whose free projects
   pause after a week and need a human to press a button.
2. **The scheduler silently stops.** GitHub disables scheduled workflows after
   60 days of repository inactivity. One email, then nothing.
3. **Cold starts look like breakage.** The first request after a quiet period
   is slow, and a naive health check calls that "down".

Each is handled below, and each has a named detection.

---

## 2. Topology — three planes, three clocks

```
  DATA          Neon Postgres (free tier)
                  ├─ pooled endpoint   ──►  read by the web app
                  └─ direct endpoint   ──►  written by the scheduled job

  SERVING       Vercel (Hobby) — Next.js, connects as ii_web (SELECT-only)

  SCHEDULING    GitHub Actions cron — Python ingestion, 02:30 UTC daily
```

This is the architecture from [Phase 2](02-architecture.md) unchanged, which is
the point: ingestion, computation and serving already ran on different clocks
locally, so deployment is a change of address rather than of design. No serving
path triggers an external fetch, so public traffic scales against our own
database rather than the SEC's rate limit.

---

## 3. Decision — two connection strings, and which gets which

**Neon offers a pooled endpoint (`-pooler` in the host) and a direct one. They
are not interchangeable, and picking one for everything is a mistake either
way.**

| | Endpoint | Why |
|---|---|---|
| Web app | **Pooled** | Serverless functions open many short-lived connections. Neon's free tier caps direct connections low enough that a traffic spike would exhaust them. The pooler exists for exactly this shape. |
| Ingestion job | **Direct** | One long-lived process doing a bulk write. Pooling buys nothing, and the pooler runs pgbouncer in transaction mode, which breaks server-side prepared statements — psycopg prepares automatically after five executions of the same query, and ingestion runs the same INSERT thousands of times. |

The second row is the one that would have cost an afternoon to debug. The
failure is not at connect time; it appears partway through a backfill once the
prepare threshold is crossed, which reads as "ingestion randomly breaks".

**Row-level security survives the pooler.** `app.current_user_id` is set with
`set_config(..., true)` — transaction-local, not session-local — so transaction
pooling cannot leak one user's setting into another user's query. That was not
foresight about pgbouncer; it was correctness at the time, and it happens to
be what makes this safe. Worth stating explicitly so nobody "simplifies" it to
a session-level `SET` later.

---

## 4. Decision — migrations run from the scheduled job, not on deploy

Vercel builds on every push. If migrations ran at build time they would run on
every deploy, from an environment with no operator watching, against an
append-only financial store.

They run as the first step of the ingest workflow instead, which is a
deliberate, logged, manually triggerable place (`workflow_dispatch`) where the
output is read. `migrate()` is already idempotent and records a SHA-256 of each
file, so drift is detected rather than silently re-applied.

The tradeoff: a deploy can briefly run against a schema older than the code.
For a read-only site that adds columns forward-only, that degrades to a missing
field, not corruption — and the alternative risks the store itself.

---

## 5. Decision — GitHub Actions, not Vercel Cron

Recorded in [ADR 004](05-technology-stack.md) and reconfirmed here for a reason
that only became concrete at deployment: **the ingestion is Python and Vercel
Cron invokes a Vercel function.** Running it there would mean either porting
ingestion to TypeScript or standing up a Python runtime beside the Next.js app,
to gain a scheduler we do not need.

Public repositories get unlimited Actions minutes, so this is free.

Vercel Cron stays the documented fallback, because it does not share the
60-day-disable failure mode. Once daily is enough for a filings job.

---

## 6. The circularity, and where detection actually lives

**A scheduler cannot detect its own failure to run.** The `health` step inside
`ingest.yml` is genuinely useful when the workflow runs and the ingestion
fails. It is worthless when the workflow is disabled, because it does not run
either — and "disabled" is indistinguishable from "nothing to do" from the
inside.

So detection lives outside the scheduler, in two places that do not depend on
it firing:

- **`GET /api/v1/health` returns 503** when any job is stale or has never run,
  so a free external uptime monitor alerts without parsing a body. Nothing
  monitored is also 503 — a system with no declared expectations is not
  healthy, it is unobserved.
- **The freshness banner on every page**, so a human looking at the site sees
  stale data as stale rather than as fact.

Point a free uptime monitor at `/api/v1/health`. That monitor is the only
component in the system that is outside the system.

---

## 7. Secrets — what lives where

| Secret | Where | Why there |
|---|---|---|
| `DATABASE_URL` (direct, write role) | GitHub Actions secret | Only the ingestion job writes |
| `DATABASE_URL` (pooled, `ii_web`) | Vercel environment variable | Serving is SELECT-only; a leaked serving credential cannot rewrite history |
| `PAYMENTS_*`-style app secrets | n/a | none in this service |

Two separate credentials with different privileges, not one shared superuser.
Invariants 9 and 10 are enforced by the database rather than by the application
being careful, and that only holds if the deployed roles are actually distinct.

`ii_web` must be created on Neon by hand — it needs a password, and a password
in a migration is a password in git. The `make web-role` target does this
locally; the same SQL runs against Neon once.

---

## 8. Cold starts

First request after 5 minutes idle pays Neon's resume, a second or so. The web
pool already allows for it (`connectionTimeoutMillis: 15_000`) and the comment
in `web/lib/queries.ts` says why.

This is acceptable for a screener nobody is paying for. It would not be
acceptable for the private broker path in Phase 21, which is one more reason
that path is fenced off.

---

## 9. What this phase does not do

**Email is still not sent.** Phases 13 and 16 are built and inert: magic-link
sign-in mints tokens nobody receives, and operational alerts accumulate
undelivered. Wiring a provider is a decision that has not been made, and it is
not in this phase's exit criteria. It is recorded in
[backlog.md](backlog.md) rather than smuggled in here.

No custom domain. The Vercel subdomain satisfies "reachable by a stranger", and
a domain costs money, which fails "monthly cost is zero".

---

## 10. Manual steps — the parts that need an account

These cannot be scripted from here and need the repository owner:

1. Create a Neon project; copy **both** the pooled and direct connection
   strings.
2. Run the `ii_web` role SQL against it (see §7).
3. Add `DATABASE_URL` (direct) as a GitHub Actions secret.
4. Import the repo on Vercel; set `DATABASE_URL` (pooled) as an environment
   variable; set the root directory to `web/`.
5. Point a free uptime monitor at `/api/v1/health`.

Everything else — migrations, seeding, the first backfill — runs from the
`ingest` workflow via `workflow_dispatch`.

---

## 11. Exit criteria, and how each is checked

| Criterion | How it is verified |
|---|---|
| Reachable by a stranger | Load the Vercel URL in a private window, run a screen, expand provenance |
| Scheduled ingestion runs in the deployed environment | `workflow_dispatch` the ingest job, then confirm a second run fires on cron unattended |
| Monthly cost is zero | Neon usage under 100 CU-hours and 0.5 GB; Vercel Hobby; public-repo Actions |
| Scheduler-did-not-fire is detected | Backdate `last_success`, confirm `/api/v1/health` returns 503 and the uptime monitor alerts |

The fourth is the only one that needs deliberate breaking to test, and it is
the one most likely to be assumed rather than checked.
