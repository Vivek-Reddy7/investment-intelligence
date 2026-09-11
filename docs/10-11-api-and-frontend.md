# Phases 10 & 11 — Read API and Frontend

Status: **complete, running locally**
Last updated: 2026-09-11

Next.js on Vercel per ADR 006, reading the Postgres loaded in Phase 7.
40 API contract checks passing, 118 Python tests still green.

---

## 1. The API

Versioned `/api/v1/...` from the first commit, because renaming a path later
breaks whatever is already calling it.

| Route | Returns |
|---|---|
| `GET /api/v1/meta` | Metric definitions, available fiscal years and currencies |
| `GET /api/v1/screen` | Screen results, live or as of a past date |
| `GET /api/v1/company/:id` | Financial history and metrics, as of a date |
| `GET /api/v1/explain?facts=` | The provenance chain for a metric's inputs |

**Read-only, and not as a matter of discipline.** There is no mutating route,
and the app connects as `ii_web`, a role that inherits `ii_app` — `SELECT`
only. Verified directly:

```
$ psql -U ii_web -d ii_dev -c "SELECT count(*) FROM metric_values"   → 1404
$ psql -U ii_web -d ii_dev -c "DELETE FROM financial_facts"
ERROR:  permission denied for table financial_facts
```

That makes invariant 9 a property of the deployment rather than a promise in a
comment. A bug in the read path cannot corrupt the store.

**Every response carries freshness.** The envelope is
`{ data, meta: { as_of_server, freshness, ... } }`, and `meta.freshness` comes
from the ingestion run log. A number without a date on it is a claim we cannot
support, and the honest answer is sometimes "four days stale, ingestion
failing".

**All SQL lives in `lib/queries.ts`.** Nothing in that file imports from
`next`, so extracting the API into its own service later is a move, not a
rewrite (architecture §2.8). Route handlers parse the request, call one
function, and shape the response.

## 2. The frontend

Server components throughout. The pages call the service layer directly rather
than fetching their own API over HTTP, which would be a pointless round trip,
and the browser gets no database access of any kind.

**The as-of date field is the product.** Everything else on the screener is
conventional. That one input is what no free Indian tool offers, and the page
labels which mode it is in — a `historical` pill when a date is set, `live`
otherwise — because a screen that silently used restated figures would look
identical to one that did not.

**Provenance is one click from every number.** `show inputs` expands to the
line items, their values, the date each was reported, and a link to the SEC
document. Collapsed by default, because most readers will not open it — but
present, or the Phase 1 promise is decoration.

**The disclaimer is on the page, not in a footer.** SEBI's Research Analyst
regulations are why this presents figures and computes ratios and does not
recommend anything (Phase 1 §3), and saying so plainly is cheaper than being
ambiguous about it.

**The limitations are on the page too**: figures are as filed with the SEC
under IFRS or US-GAAP rather than Ind AS, coverage is the small set of Indian
companies that file with the SEC, and periods are annual. A reader comparing
to Screener.in will see different numbers, and they should find out from us
rather than from the discrepancy.

## 3. The bug the live path found

The historical screen worked and the live screen returned a 500:

```
could not determine data type of parameter $1
```

The `as_of` parameter is bound on both paths but only *referenced* in the
historical SQL, so on the live path Postgres had a parameter it could not
type. Fixed by referencing it through `COALESCE($1::timestamptz, now())` on
both paths, which also removed a branch.

Worth noting how it presented: the feature that is the whole product worked,
and the ordinary case was broken. Testing the interesting path first is a good
instinct that hides exactly this.

Also fixed: `pg` returns `bigint[]` as strings, because a bigint can exceed
JavaScript's safe integer range. Fact ids will not, so they are converted at
the service boundary — the declared type was `number[]` and had been quietly
lying.

## 4. Contract tests

`web/scripts/contract-test.mjs`, run against a live server. 40 checks, all
passing. Not a unit suite — it checks the things a frontend breaks silently
on:

- Envelope shape, and that freshness is always present
- Live and historical paths both work, with the flag echoed
- An as-of date before anything was filed returns **nothing**, rather than
  today's answer
- Provenance rows name their filing, their licence and their `known_from`
- Malformed input is refused with 400 and an explanation

Five of the checks are injection attempts — `ROE:; DROP TABLE
financial_facts; --`, a threshold of `1;DELETE`, a fact id of `1;drop`. All
refused by the allow-lists, and a final check confirms the store is intact
afterwards. A screen is user input reaching a `WHERE` clause, so that is
asserted rather than assumed.

## 5. Limitations

- **Not deployed.** Phase 12. Runs on `localhost:3100` against local Postgres;
  no Neon project exists yet.
- **No charts.** The exit criterion says charts, and there are tables. With
  annual-only data and ten companies, a sparkline over four points would be
  decoration. Worth adding when quarterly data exists.
- **Two criteria in the form**, though the API accepts eight. A full query
  builder is not the interesting part.
- **No search.** Ten companies; the screener is the navigation.
- **Cold start unmeasured.** Neon suspends after 5 minutes idle and resumes on
  connection, and that latency has never been observed because we are on local
  Postgres.

---

## Exit criteria

**Phase 10**
- [x] Serves entirely from the store; no external fetch on any serving path
- [x] Every response carries data freshness
- [x] Documented, stable response shapes, versioned path
- [x] Read-only enforced by database privilege

**Phase 11**
- [x] Screener UI with the as-of-date control
- [x] Company pages with financial and metric history
- [x] Provenance visible from every number
- [ ] Charts — deferred, see §5
- [ ] *"A stranger can screen and read a company page unaided"* — plausible
      but untested; nobody outside this session has tried it, and it is not
      reachable until Phase 12
