# Phase 14 — Testing Strategy

Status: **complete**
Last updated: 2026-09-11

179 tests, 91% line coverage on the Python package, CI gating merges.

Coverage is the least interesting number here. This phase exists because the
failures that matter in this system **produce no error and no wrong-looking
output** — a restated figure silently overwriting the original, a historical
screen quietly using today's numbers, a scheduler that stopped firing. A suite
at 100% coverage that does not name those is worth less than one at 60% that
does.

So the real deliverable is §2: an inventory of every silent failure, each with
the test that catches it.

---

## 1. What is tested, and how

| Layer | Approach | Why |
|---|---|---|
| Schema, triggers, privileges | Real PostgreSQL, per-test transaction rollback | The correctness guarantees *are* database features. A fake would test the fake |
| Point-in-time queries | Hand-seeded data including a restatement and a delisting | The two cases the product exists for |
| Source adapters | A shared contract suite, parameterised over every adapter | §2.2 claims adapters are interchangeable; this is what makes that true |
| EDGAR adapter | A cached `companyfacts` response | No network in CI. A red build from someone else's outage teaches people to ignore red builds |
| Ingestion engines | A fixture source that can fail on demand | You cannot ask a real API to fail on the fourth company |
| Metrics | Arithmetic against known inputs | 100% covered; the formulas are the product |
| API | Contract test against a running server | Checks what a frontend breaks silently on |
| Configuration | Invariant assertions over `STAGE_ONE` | A duplicate CIK would merge two companies' histories |

Ingestion tests needed a second fixture, `committed_conn`, because the
backfill **must** commit — an uncommitted checkpoint is not a checkpoint. It
rebuilds the schema around each test via `DROP SCHEMA`, which is DDL and
bypasses the append-only triggers. That is the correct boundary: the triggers
protect against DML, and in production the application roles do not own the
schema.

## 2. The invisible-failure inventory

Every row is a failure that produces no exception, no stack trace, and output
that looks entirely reasonable. This table is the thing to review when
changing anything — not the coverage percentage.

| # | Silent failure | What you would see | Named test |
|---|---|---|---|
| 1 | A restatement overwrites the original figure | A plausible number; the original simply gone | `test_restatement_creates_a_version_rather_than_an_overwrite` |
| 2 | A historical query returns today's view | A flattering backtest | `test_as_of_before_restatement_returns_the_original_figure` |
| 3 | `known_from` set to ingestion time, not filing date | Every past date returns the same answer | `test_the_restated_version_is_dated_by_its_filing_not_by_the_run` |
| 4 | Delisted companies dropped from history | Survivorship bias; results always improve | `test_delisted_company_is_present_for_dates_before_it_delisted` |
| 5 | A reused ticker merges two companies | One continuous, fictional history | `test_a_ticker_alone_cannot_identify_an_instrument` |
| 6 | A duplicate CIK in config merges two companies | Same, via configuration | `test_no_cik_is_listed_twice` |
| 7 | A metric computed across two currencies | A margin that is an exchange-rate artefact | `test_a_metric_is_never_computed_across_currencies` |
| 8 | An undefined ratio emitted as a number | Rankings led by companies with broken figures | `test_return_on_negative_equity_is_omitted` + 3 others |
| 9 | Fiscal year taken from the filing, not the period | Three unrelated "FY2019 revenues" coexisting | `test_fiscal_year_comes_from_the_period_not_the_filing` |
| 10 | Balance-sheet items silently dropped | Leverage metrics absent, no error | `test_balance_sheet_items_are_not_dropped` |
| 11 | An interim balance placed on the annual period | December's balance sheet reported as March's | `test_unplaceable_balance_dates_are_rejected_not_guessed` |
| 12 | `TRUNCATE` bypassing append-only | Table empty, command reported success | `test_truncate_cascade_is_refused_by_the_trigger` |
| 13 | A company missing a metric passes a screen on NULL | Results that do not meet the criteria | `test_a_screen_requires_every_criterion` |
| 14 | Live and historical metric definitions drift | Different answers by date, looking like the feature working | `test_the_materialised_view_agrees_with_the_function` |
| 15 | The scheduler stops firing | Stale data, every page rendering fine | `test_health_reports_a_job_that_has_never_run` |
| 16 | "Never ran" reported when it is "never succeeded" | Investigating the scheduler when the source is at fault | `test_a_job_that_ran_but_never_succeeded_is_not_reported_as_never_run` |
| 17 | The daily job re-ingesting everything | Works, and gets slower every year | `test_a_second_cycle_writes_nothing_and_fetches_nothing_new` |
| 18 | Metrics lagging the facts they derive from | Two numbers disagreeing, nothing saying which to trust | `test_metrics_are_refreshed_after_ingestion` |
| 19 | An empty universe emptying every screen | "No companies match" — an ordinary answer | `test_a_company_with_a_filing_is_in_the_universe` |
| 20 | "No gaps" reported when nothing was ever planned | Reassurance about a job that never started | `test_gaps_distinguishes_unplanned_from_complete` |
| 21 | A test double diverging from the real adapter | Engine tests validating behaviour production lacks | `test_an_unknown_reference_yields_a_rejection_rather_than_raising` |
| 22 | An adapter forging transaction time | Fabricated history, internally consistent | `test_no_adapter_sets_known_from_itself` |
| 23 | A content-hash collision hiding a restatement | The restatement never ingested | `test_content_hash_distinguishes_revisions` |
| 24 | A float used for money | Precision lost invisibly | `test_a_float_value_is_refused` |

**Rows 19, 20, 21 and 16 were added in this phase, and three of them are bugs
this phase found.** Two were found by writing the contract test, one by wiring
CI. That is the return on the phase: not the tests, the bugs.

## 3. Three bugs found while writing the tests

### 3.1 The test double had diverged from the real adapter

`FixtureSource` yielded **nothing** for an unknown reference; `EdgarSource`
yielded a `Rejection`. Both were individually tested and correct against their
own expectations, which is exactly why neither test caught it.

It matters because the engine tests all run against the fixture. They had been
validating behaviour production does not have. The contract test found it on
its first run.

The distinction the engine needs: a reference present with an empty document
list is a **known company with nothing to load** (`SKIPPED`); a reference
absent entirely is a **configuration gap**. Collapsing them is the same
mistake as collapsing `DONE` into `SKIPPED`.

### 3.2 `gaps()` said "no gaps" when nothing had been planned

`gaps()` returns an empty dict in two opposite situations: every instrument
`DONE`, and no checkpoints existing at all. The CLI reported *"no gaps: every
tracked instrument is DONE"* for both.

Found while wiring CI, where the fixture loader used a different job name.
Same fail-quiet shape as the Phase 9 empty-universe bug: the reassuring answer
to the wrong question. Now it reports the count — so "all DONE" over an empty
set is impossible to state — and exits non-zero when nothing is planned.

### 3.3 A racy test, found in Phase 8 and worth naming here

The staleness test shrank `max_age` to one second immediately after a
successful run, so `age` was often still under the threshold and the test
flapped to `OK`. It now backdates the run instead, which is deterministic and
a more honest model of the real scenario.

A flaky test is worse than a missing one: it teaches people to re-run CI.

## 4. What is deliberately not tested, and why

Being explicit here, because an untested thing that nobody has decided about
is different from one that has been.

| Not tested | Why |
|---|---|
| **The live SEC HTTP path** | Would make CI depend on the SEC's uptime and hammer a rate-limited public service. The cached fixture covers our interpretation; the transport is `urllib`. Risk accepted: a change to SEC's URL shape or auth would be caught only by the daily job failing — which is what the health check is for |
| **Frontend rendering** | No component tests. Server components that call the service layer directly are nearly all data-shaping, and the API contract test covers the data. A broken page is loud, not silent — which puts it outside this phase's remit |
| **Neon-specific behaviour** | Cold starts and connection suspension cannot be reproduced against local Postgres. Unverified until Phase 12 |
| **Vercel deployment** | Same |
| **Concurrency** | Two simultaneous ingestion cycles. The workflow uses a `concurrency` group so it should not happen, and writes are idempotent so it should be survivable. "Should" twice; untested |
| **Index performance** | 2,393 facts is not enough volume for `EXPLAIN` to mean anything. A Phase 19 input |
| **`metrics_as_of` at scale** | A historical screen recomputes the whole pivot. Real cost unmeasured |
| **Migration rollback** | There is none, by design (`db/README.md`). Forward-only |
| **The 60-day Actions auto-disable** | Cannot be tested; it takes 60 days. Mitigated out of band by `/api/v1/health` and the freshness banner |

## 5. CI

`.github/workflows/ci.yml`, three jobs, red blocks merge:

- **python** — Postgres 17 service container, full suite, coverage floor at 80%
- **web** — typecheck and production build
- **contract** — migrates, seeds, loads the cached fixture, starts the server, runs the 40 API checks

The coverage floor is a floor, not a target. Chasing a high number rewards
testing the trivial; the gate that matters is the §2 inventory, reviewed by a
human.

## 6. Coverage, for the record

```
analytics/screen.py     100%     ingest/writer.py         98%
companies.py            100%     db.py                    98%
ingest/backfill.py       96%     ingest/incremental.py    93%
sources/fixture.py       96%     sources/base.py          91%
sources/edgar.py         89%     cli.py                   72%
                                 TOTAL                    91%
```

`edgar.py`'s missing lines are the live HTTP path (§4). `cli.py`'s are output
formatting in commands whose behaviour is covered through the modules they
call.

---

## Exit criteria

- [x] Unit tests on metric computation — 100% on `analytics/screen.py`
- [x] Contract tests on source adapters — shared suite over all adapters
- [x] Integration tests on ingestion idempotency and point-in-time queries
- [x] End-to-end on the screening path — API contract test
- [x] **The invisible-failure cases have named tests** — §2, 24 rows
- [x] A written note on what is deliberately untested and why — §4
- [x] CI blocks merge on red
