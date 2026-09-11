# Phase 8 — Incremental and Scheduled Ingestion

Status: **complete** (the schedule itself activates at deployment)
Last updated: 2026-09-11

The job that runs forever unattended. 134 tests.

---

## 1. Why this is a separate phase from backfill

|  | Backfill (Phase 7) | Incremental (this) |
|---|---|---|
| Runs for | hours | ~8 seconds |
| Window | ten years | recent |
| Interruption | expected, checkpointed | retry next cycle |
| Failure mode | partial load | **silent staleness** |

That last row is the whole reason. A backfill that half-finishes is visible —
the checkpoint table says so. An incremental job that stops running leaves
**no evidence at all**: the site keeps serving yesterday's numbers,
confidently, and every page still renders.

So this module's real output is not the facts it writes. It is the
`ingestion_runs` row it always writes, and the `ingestion_health()` check that
notices the absence of one.

## 2. Cheap when there is nothing to do

Verified against live EDGAR: **10 companies, 8 seconds, 0 facts written.**
Most of that is the deliberate 0.5s rate limit.

The job loads the accession numbers it has already parsed and skips those
documents, so daily cost is proportional to what is *new* rather than to the
whole history. Relying on the writer's dedup alone would be correct and would
get slower every year.

A test runs seven consecutive cycles and asserts nothing accumulates — not
versions, not filings.

**Lookback is two years, not one.** A restatement can revise a period well
after it closed; the Sify restatement found in Phase 7 arrived eighteen months
late. Narrowing the window to save time would silently stop catching the thing
the product exists for.

## 3. Detecting the run that did not happen

`ingestion_schedule` declares what we *expect* to run and how stale is too
stale. `ingestion_health()` compares expectation against the run log.

This is the only way an absent run is visible. A query over `ingestion_runs`
can only describe runs that happened; the interesting question is the absence
of one.

Six states, and the distinctions matter:

| Status | Means | Where to look |
|---|---|---|
| `NEVER_RAN` | No run of any kind | **The scheduler** — disabled workflow, wrong cron, missing secret |
| `NEVER_SUCCEEDED` | Runs happened, none worked | **The source** — the job fires, the provider refuses us |
| `STALE` | Last success is older than `max_age` | Either |
| `LAST_RUN_FAILED` / `LAST_RUN_PARTIAL` | Recent success, latest cycle degraded | The source |
| `OK` | — | — |

Migration 015 conflated the first two by testing `last_success` before
`last_outcome`, so a job that had run repeatedly and failed every time
reported `NEVER_RAN` — which would send anyone investigating to the wrong
place. Migration 016 separates them. Found by the tests, not in production.

**`health` exits non-zero when nothing is scheduled.** A system with no
declared expectations cannot be healthy; it can only be unobserved.

## 4. The circularity, and how it is broken

`.github/workflows/ingest.yml` runs the ingestion and then the health check.
That is useful when the workflow runs and the ingestion fails.

**It is worthless when the workflow itself is disabled, because the health
check does not run either.** GitHub silently disables scheduled workflows
after 60 days of repository inactivity (ADR 004) — one email, then nothing.

**A scheduler cannot detect its own failure to run.** Detection has to live
where the scheduler does not, so it lives in two places:

1. **The freshness banner on every page.** Already built in Phase 11; a reader
   sees "data N days stale".
2. **`GET /api/v1/health`**, which returns **503** when any expected job is
   stale or has never run, so a free external uptime monitor alerts without
   parsing the body. Verified:

```
$ curl -w '%{http_code}' /api/v1/health          → 200  {"status":"OK"}
# after making the schedule stale
$ curl -w '%{http_code}' /api/v1/health          → 503  {"status":"DEGRADED"}
```

"Nothing is monitored" is also 503, deliberately.

Vercel cron is the documented fallback scheduler: once-daily is adequate for a
post-close job and it does not share GitHub's 60-day failure mode.

## 5. Derived state must not lag the facts

Each cycle refreshes `metric_values` after ingesting. Fresh facts behind stale
ratios is worse than being uniformly stale, because the two disagree and
nothing on the page says which to trust.

## 6. Limitations

- **The schedule is not running.** The workflow file exists; it starts firing
  when the repo is public and `DATABASE_URL` is set as a secret. Phase 12.
- **"Runs unattended for a week" is asserted, not observed.** The tests
  compress seven cycles into one run. Real unattended operation needs
  deployment.
- **Annual data means most cycles find nothing.** That is expected, and it is
  precisely why the run-log row and the health check matter more here than the
  facts written.
- **No alerting.** `health` exits non-zero and the endpoint returns 503;
  nothing is wired to notify anyone. Phase 16.

---

## Exit criteria

- [x] Scheduled workflow, with dedup on natural key plus value
- [x] Run-log rows for every attempt including failures and idle cycles
- [x] A simulated restatement produces a new version, not an overwrite
- [x] Restated versions dated by their filing, not by the run
- [x] Expected-run gap detection, with out-of-band exposure
- [ ] *Runs unattended for a week* — needs Phase 12
