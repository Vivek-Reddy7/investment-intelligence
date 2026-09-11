# Phase 16 — Observability

Status: **complete; notification sending needs a channel (Phase 12)**
Last updated: 2026-09-11

319 tests. Structured logging with run correlation, an operational status
page, and detection that becomes a notification exactly once.

---

## 1. What already existed, and what was missing

Phase 8 built the hard part — `ingestion_health()`, which reports on an
*expectation* and so can see a run that did **not** happen. Phase 11 put a
freshness banner on every page.

What was missing was everything between detection and a human:

| Needed | Status before | Now |
|---|---|---|
| Detection of staleness | Done (Phase 8) | — |
| User-visible staleness | Done (Phase 11) | — |
| **Structured logs with a run id** | **Absent** | `observability/logging.py` |
| **Detection → notification** | **Absent** | `app.operational_alerts` |
| **An operational view** | **Absent** | `/status` and `cli status` |
| **Ingestion metrics** | **Absent** | `ingestion_metrics` view |
| Sending the notification | Absent | Still absent — needs a channel |

## 2. The structured logging I should have done in Phase 6

Architecture §2.8 said *"structured JSON logs with a request/job ID from the
first commit"*, and then sixteen phases happened without it. That is how that
item usually goes, and the cost is real: retrofitting observability is
miserable precisely because the correlation id has to be threaded through
everything that already exists.

Two properties matter more than the format:

**Correlation.** Every line emitted during a run carries that run's id, held
in a `ContextVar` so it does not have to be passed through every signature. A
log without one is a pile of statements; with one, *"what happened in run 47"*
is a grep.

**Redaction by key name**, not by inspecting values — a token looks like any
other string. `token`, `password`, `database_url`, `email`, `authorization`,
`cookie` and friends are replaced before serialisation. A token in a log is a
credential somewhere nobody protects: CI logs are readable by anyone with
repository access, and aggregators outlive the secrets they hold.

Threading it through `incremental.run` forced a small refactor — the context
manager plus the per-instrument loop plus the teardown made one function that
had to be read three times to follow, so the loop is now `_run_cycle`.

## 3. Detection becomes a notification, once

`app.detect_operational_alerts()` turns `ingestion_health()` into alert rows,
**edge-triggered**, reusing both patterns from Phase 13's user alerts because
both were right there and are right here:

- **Once per transition.** A job STALE for a week notifies once, not seven
  times. An alert that fires every cycle while a condition holds trains
  whoever receives it to filter the channel — which is strictly worse than no
  alerting, because the next real one is filtered too.
- **Firing and sending are separate.** Detection records; a sender drains
  `pending_notifications()`. A failing channel can then neither lose an alert
  nor stall detection. A notification failure is recorded and leaves the alert
  **open**, which a test asserts.

Alerts also **resolve** when the condition clears, and the resolved row is
kept rather than deleted. Without resolution the status page shows a permanent
red light for something already fixed, which is how a dashboard stops being
read. The history of what broke is worth keeping.

The `detail` field says where to look, because the diagnosis is the useful
part:

```
NEVER_RAN        "Look at the scheduler: a disabled workflow, a wrong cron,
                  or a missing secret."
NEVER_SUCCEEDED  "Runs are happening and none has succeeded. Look at the
                  source, not the scheduler."
STALE            "Last success was 10 days ago, past the 3 days limit. The
                  site is serving stale numbers."
```

## 4. The exit criterion, demonstrated

Verified end to end against the real dataset:

```
$ cli status                          → OVERALL: OK                  exit 0
# backdate the last success past the limit
$ cli status
{"ts":"…","level":"ERROR","message":"operational alert opened",
 "source_id":"SEC_EDGAR","kind":"FUNDAMENTALS","status":"STALE"}
OVERALL: CRITICAL
  [CRITICAL] SEC_EDGAR/FUNDAMENTALS: STALE  (NOT NOTIFIED)
      Last success was 10 days ago, past the 3 days limit.
                                                            exit 1
# run it four more times
alerts_total | open
           1 |    1                    ← edge-triggered
# fix it
{"message":"operational alert resolved", …}
OVERALL: OK                                                 exit 0
```

## 5. Two things the status page does differently

**It is not the freshness banner.** The banner answers *"is what I am reading
current?"* for a visitor, in one line. The status page answers *"is the system
working?"* and shows the failure's shape. Those want different things.

**A render has no side effects.** Detection runs in the scheduled job, not on
page load, so a crawler hitting `/status` cannot open alerts. That was
tempting to do the other way — it would have made the page self-updating — and
it would have meant a search engine writing rows.

The page is **public**, deliberately. Everything on it is already inferable
from the site, and publishing it makes staleness impossible to miss rather
than something only we can see. It carries no connection string, no user data
and no secret.

## 6. An inert check degrades the verdict

Phase 17's rule, enforced at the top level: zero findings over zero evaluable
periods is an inert check, not clean data. So the overall verdict cannot read
`OK` while a quality check evaluated nothing, and `/status` names the inert
checks explicitly. An empty database correctly reports `UNMONITORED` with all
six checks inert.

Also surfaced: `still_running` in `ingestion_metrics`. A process that died
without finishing leaves a run stuck in `RUNNING`, which no outcome-based
query notices.

## 7. The Phase 15 guards earned their place again

Migration 022 added one table, and two Phase 14/15 tests failed immediately:
`operational_alerts` was not row-level-secured, and `ii_alerts` held grants
nobody had declared.

Both were correct and both needed a written reason rather than a silent
addition — that is the third time those guards have caught a new table. Added
alongside: a test that `ii_alerts`, the one role that works across all users,
cannot reach a watchlist, a portfolio or a saved screen.

## 8. One bug worth naming

The status page rendered coverage dates as
`Tue Mar 31 2015 00:00:00 GMT+0530 (India Standard Time)`.

Cosmetic on the surface and a correctness bug underneath: `pg` returns a
PostgreSQL `DATE` as a JavaScript `Date`, which then renders through the
viewer's offset — enough to display the **wrong day** for a date near
midnight. A `DATE` has no timezone, so the fix is `::text` in SQL to avoid the
conversion rather than correct it.

## 9. Limitations

- **Nothing sends the notifications.** Alerts are detected, recorded and
  exposed; no email or webhook is wired, because that needs a provider key
  (Phase 12). Until then the channels are the status page, the 503 on
  `/api/v1/health`, and the CLI's exit code.
- **No metrics backend.** No Prometheus, no OpenTelemetry, no traces. The
  metrics are a SQL view, which is right at this scale and is not distributed
  tracing.
- **Logs go to stdout and nowhere else.** In GitHub Actions that means they
  live as long as the run retention. No aggregator, so "what happened three
  weeks ago" is answerable from the run log and the alert history, not from
  logs.
- **Detection runs only when something calls it.** In the scheduled workflow
  that is once a day, so the worst-case time-to-alert is a day plus the
  staleness threshold. Acceptable for annual filings; wrong for anything
  faster.
- **No log-based alerting.** A warning line for one failed instrument does not
  raise anything; only the aggregate outcome does.

---

## Exit criteria

- [x] Structured logging, with run correlation and redaction
- [x] Ingestion metrics
- [x] Data-freshness monitoring *(Phase 8, surfaced here)*
- [x] An internal status page — `/status` and `cli status`
- [x] **A deliberately broken ingestion run produces an alert within one
      cycle** — demonstrated in §4, and four tests covering never-ran,
      never-succeeded, stale, and healthy
- [x] **Staleness is visible to users** — freshness banner (Phase 11) plus a
      public status page
- [ ] Alerting *delivers* — detection is done; the channel is Phase 12
