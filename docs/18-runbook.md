# Runbook

What to do when something breaks. Organised by **what you saw**, because that
is what you have when you arrive — not by component, which is what you want
once you already know the answer.

Every failure mode here has detection built for it. If you are reading this
about something not listed, that is itself worth noting: it means something
failed in a way nothing was watching for.

---

## First: `make status`

One command, whole picture. It exits non-zero when the verdict is `CRITICAL`
or `UNMONITORED`, so it also works as a check.

```
OVERALL: OK | DEGRADED | CRITICAL | UNMONITORED
```

| Verdict | Means |
|---|---|
| `OK` | Ingestion current, every quality check evaluated something |
| `DEGRADED` | A job is off, or a quality check is inert |
| `CRITICAL` | An open critical alert — data is stale or never loaded |
| `UNMONITORED` | **Nothing is scheduled.** Not the same as working |

---

## "The site shows old numbers"

The failure this system is built to make loud. Every page still renders, so
nothing looks broken.

1. `make health` — one line per expected job.
2. The status tells you **where to look**, and the distinction matters:

| Status | Look at | Because |
|---|---|---|
| `NEVER_RAN` | **The scheduler** | No run of any kind happened |
| `NEVER_SUCCEEDED` | **The source** | Runs are happening; none works |
| `STALE` | Either | Last success is past the limit |
| `LAST_RUN_FAILED` / `PARTIAL` | The source | Recent success, latest cycle degraded |

Reporting `NEVER_RAN` when it is `NEVER_SUCCEEDED` sends you to the wrong
place, which is why migration 016 separates them.

### If `NEVER_RAN`

Almost always the scheduler, in this order:

1. **GitHub disabled the workflow.** Scheduled workflows are disabled after 60
   days of repository inactivity — one email, then silence. Actions → `ingest`
   → Enable workflow.
2. **The `DATABASE_URL` secret is missing or wrong.** The run will appear in
   Actions and fail at the migration step.
3. **The cron never fired.** Execution is not punctual; 10–30 minute delays are
   normal. An hour late is not yet a problem.

**The workflow cannot detect its own absence** — its health step does not run
either. That is why detection also lives out of band: the freshness banner on
every page, and `GET /api/v1/health` returning 503 for an external monitor.

### If `NEVER_SUCCEEDED` or `LAST_RUN_FAILED`

The source, not us.

```bash
make incremental      # run a cycle by hand and watch it
make gaps             # which instruments are not DONE
```

Common causes: SEC EDGAR unreachable or rate-limiting (we take one request per
0.5s against a 10/s limit, so this is unlikely unless something changed), or a
`companyfacts` response shape change — which would show as rejections rather
than a failure.

---

## "A screen returns nothing"

Ordinary and alarming in equal measure, because *"no companies match your
criteria"* is a perfectly normal thing for a screener to say.

1. **Is the universe empty?**
   ```sql
   SELECT count(*) FROM universe_as_of(current_date, now());
   ```
   Zero means every screen returns nothing. This happened in Phase 9:
   `universe_as_of` required lifecycle events the EDGAR adapter never
   ingests. Membership is now driven by filings — a company with a filing is
   screenable — and `test_a_company_with_a_filing_is_in_the_universe` is the
   canary.

2. **Do the metrics exist for that year and currency?**
   ```sql
   SELECT fiscal_year, currency, count(*) FROM metric_values GROUP BY 1,2;
   ```
   Metrics are computed **per currency**. Screening FY2019 in INR finds
   nothing if that company reports in USD.

3. **Is it a genuine miss?** Loosen one criterion. Each is its own `EXISTS`,
   so a company missing *any* screened metric fails rather than slipping
   through on a NULL.

---

## "The numbers look wrong"

```bash
make quality
```

Read the **coverage** column before the findings. Zero findings over zero
evaluable periods is an **inert check**, not clean data — the report prints
`?? INERT` and the CLI exits non-zero for it.

| Check | A finding means |
|---|---|
| `TAX_IDENTITY` | pbt − tax ≠ net profit beyond 2%. Usually a concept mapped to the wrong line item |
| `EQUITY_EXCEEDS_ASSETS` | Impossible without negative liabilities. Always an error |
| `FX_INCONSISTENT` | One period in two currencies implying two exchange rates. One figure belongs elsewhere |
| `IMPLAUSIBLE_TAX_RATE` | Often a genuine tax credit. Check whether both currencies agree — if they do, it is real |

To trace a single number, use the provenance chain: the UI's `show inputs`, or
`/api/v1/explain?facts=…`. It ends at an SEC document you can open.

---

## "Rejections went up"

```bash
make quality        # the REJECTED AT INGESTION section
```

Read the **class**, not the count. Individual reasons carry values and dates,
so raw text gives one group per rejection.

| Class | Action |
|---|---|
| `YTD_NOT_STORED` | **None.** A decision, not a failure: year-to-date periods overlap the quarters they contain |
| `UNPLACEABLE_BALANCE_DATE` | Interim balance sheet with no matching period. Usually fine |
| `SOURCE_UNAVAILABLE` | The provider 404'd. ICICI Bank always does |
| `UNRECOGNISED_PERIOD_SPAN` | A span outside every band. Investigate — this was 215 recoverable quarters once |
| `UNCLASSIFIED` | **Always investigate.** The source started rejecting for a new reason and no rule matches |

A growing `UNCLASSIFIED` bucket is the signal that something changed upstream.

---

## "Someone reports seeing another user's data"

Treat as an incident.

1. **Confirm the isolation still holds.**
   ```bash
   make test    # test_accounts.py covers cross-user isolation
   ```
2. **Check RLS is on and forced.** Enabled alone exempts the table owner:
   ```sql
   SELECT relname, relrowsecurity, relforcerowsecurity
   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'app';
   ```
3. **Check what the app connects as.** A superuser bypasses RLS
   unconditionally. The role must inherit `ii_app` and must not be the schema
   owner in production.
   ```sql
   SELECT rolname, rolsuper, rolbypassrls FROM pg_roles
   WHERE rolname IN ('ii_app','ii_web','ii_ingest','ii_alerts');
   ```

This is the failure mode where the tests were once wrong in exactly this way —
they ran as a superuser and would have passed with no policies at all.

---

## "Did someone rewrite history?"

They could not have, and it is cheap to confirm.

```sql
-- Should raise: append-only violation
UPDATE financial_facts SET value = 0 WHERE fact_id = 1;
DELETE FROM financial_facts WHERE fact_id = 1;
TRUNCATE financial_facts;
TRUNCATE filings CASCADE;    -- the one a foreign key does NOT cover
```

All four are refused by triggers, which apply to the table owner too. Separately
no application role holds `UPDATE` or `DELETE` on a fact table. Both layers
exist because each covers the other's blind spot.

---

## Routine operations

```bash
make incremental   # a cycle by hand — cheap, ~8s, no-op when nothing is new
make status        # after any change
make audit         # dependency vulnerabilities
```

**Adding a company** — add to `STAGE_ONE` in `src/investment_intelligence/companies.py`,
then `make seed && make backfill`. It must be an SEC registrant with a CIK.

**Adding a metric** — one row in `metrics` and one clause in `metrics_as_of()`
(migration 013). Add it in a new migration; do not edit an applied one, the
runner will refuse. It becomes screenable and alertable immediately, because
both validate against the table rather than a hard-coded list.

**Adding a data source** — implement `FilingSource`, add it to `ADAPTERS` in
`tests/test_source_contract.py`, and make the contract tests pass. They are
the specification. It also needs a `sources` row, which cannot exist without a
verified licence note.

---

## What has no runbook entry

Stated so the gaps are visible rather than assumed covered:

- **Deployment failures.** Nothing is deployed (Phase 12).
- **Notification delivery.** Alerts are detected and recorded; nothing sends
  them, so there is no channel to debug.
- **Restoring from backup.** No backup strategy exists. The free Neon tier has
  none, and everything except user data is reproducible from SEC EDGAR —
  which is a statement about the market data, not about the user data, and the
  user data has no answer yet.
- **Scaling incidents.** No load has been applied; Phase 19/20.
