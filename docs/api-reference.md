# API reference

Five endpoints, all `GET`, all read-only. There is no mutating route: the web
app connects as a role holding `SELECT` on market data, so invariant 9 is a
property of the deployment rather than a promise.

Base path is versioned from the first commit, because renaming one later
breaks whatever is already calling it.

## Envelope

Every successful response:

```json
{
  "data": ...,
  "meta": {
    "as_of_server": "2026-09-11T09:49:07.000Z",
    "freshness": [ { "source_id": "SEC_EDGAR", "last_success": "...", "stale_days": 0 } ]
  }
}
```

`meta.freshness` is on **every** response, from the ingestion run log. A number
without a date on it is a claim this platform cannot support, and the honest
answer is sometimes *"four days stale, ingestion failing"*.

Errors are `{"error": "..."}` with a 4xx status. Bad input is always a 400 with
an explanation, never a 500 and never a silent empty result — *"no matches"* is
an ordinary answer and would hide a typo.

---

### `GET /api/v1/meta`

Everything needed to build a screen form: metric definitions (with their
formulas, for display), available fiscal years, available currencies.

---

### `GET /api/v1/screen`

| Parameter | | |
|---|---|---|
| `fy` | required | Fiscal year, integer |
| `c` | required, repeatable | `METRIC:op:value`, e.g. `NET_MARGIN:gte:0.10`. Max 8 |
| `currency` | default `USD` | Metrics are computed per currency; mixing them is meaningless |
| `as_of` | optional | **The point of the product.** `YYYY-MM-DD`. Omit for today |
| `limit` | default 200, max 500 | |

```
/api/v1/screen?fy=2019&currency=USD&as_of=2020-06-30
              &c=NET_MARGIN:gte:0.10&c=ROE:gte:0.12
```

`as_of` does two things at once, and both are needed: it restricts the
**facts** to those known by that date, and the **universe** to companies
reporting by then. Only the first leaks survivorship; only the second leaks
lookahead.

Operators come from an allow-list (`gt gte lt lte eq ne`) and thresholds must
be numeric. A screen is user input reaching a `WHERE` clause.

Each row carries `metrics[CODE].inputs`, the fact ids the value was computed
from — feed them to `/explain`.

`meta.historical` says which path served the request. Historical screens
recompute from `metrics_as_of()` and are slower by design; they are also rate
limited, because an arbitrary as-of date is an arbitrary uncacheable
full-table computation.

---

### `GET /api/v1/company/:id`

Financial and metric history for one instrument, as of a date.
`?as_of=YYYY-MM-DD` optional. 404 for unknown, 400 for a non-numeric id.

Every figure carries its own `known_from` — the difference between *"revenue
was X"* and *"revenue was reported as X on this date, by this filing"*.

---

### `GET /api/v1/explain?facts=12,13`

The provenance chain for a metric's inputs. Max 50 ids.

Each row: the line item, its value and currency, the period, `known_from`, the
filing's `source_ref` (an SEC URL you can open), and the source's licence note.

This is the Phase 1 promise made machine-readable. A metric that cannot answer
it is a defect, not a gap in the UI.

---

### `GET /api/v1/health`

For an external uptime monitor. **503** when any expected job is stale or has
never run, so a monitor alerts without parsing the body. `UNMONITORED` — no
job scheduled — is also 503: a system with no declared expectations cannot be
healthy, only unobserved.

It exists because of a circularity: the ingestion health check runs inside the
same workflow as the ingestion, so it cannot detect that workflow being
disabled. **A scheduler cannot detect its own failure to run**, so detection
has to live somewhere the scheduler does not.

---

## What is deliberately absent

- **No authentication on the read API.** Everything it serves is public filing
  data, which is the product.
- **No pagination beyond `limit`.** Ten companies.
- **No write endpoints.** Ingestion is a scheduled job, not an API.
- **No prices.** Publishing Indian market data costs ₹1,10,000 per display
  medium ([data sources §1](03-data-sources.md)).
