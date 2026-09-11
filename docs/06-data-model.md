# Phase 6 — Data Model and Schema

Status: **complete**
Last updated: 2026-09-11
Depends on: [02-architecture.md](02-architecture.md), [05-technology-stack.md](05-technology-stack.md)

Nine forward-only SQL migrations in [`db/migrations/`](../db/migrations/),
46 tests. This is the first phase with code in it.

---

## 1. The two time axes

Every fact in this system carries two dates, and keeping them distinct is the
whole design.

| Axis | Column | Question it answers |
|---|---|---|
| **Valid time** | `period_start` / `period_end`, `event_date`, `announced_on` | *When was this true in the world?* |
| **Transaction time** | `known_from` | *When did we learn it?* |

A query filters on transaction time to reproduce a past belief, and on valid
time to select a period. Both are needed, and confusing them is the bug the
product exists to avoid.

Worked example. Infosys reports Q2 FY2026 revenue of 1000 on 14 Oct 2026, then
restates it to 900 on 20 Jan 2027:

| Row | valid time | `known_from` | value |
|---|---|---|---|
| 1 | Q2 FY2026 | 2026-10-14 | 1000 |
| 2 | Q2 FY2026 | 2027-01-20 | 900 |

- As of 1 Nov 2026 → **1000**. The restatement did not exist.
- As of 1 Mar 2027 → **900**.
- As of 1 Sep 2026 → **nothing**. The quarter had not been reported.

That third case is the one screeners get wrong, and it is lookahead bias.

## 2. Tables

### Append-only — what companies reported

| Table | Holds | Fact identity |
|---|---|---|
| `financial_facts` | Every reported line item, versioned | instrument + basis + fiscal year + period type + line item |
| `filings` | The documents facts came from | source + reference + content hash |
| `instrument_identifiers` | Ticker and name history | instrument + exchange + effective_from |
| `instrument_lifecycle` | Listed, suspended, delisted | instrument + event + event_date |
| `corporate_actions` | Splits, bonuses, dividends | instrument + type + announced_on |

### Mutable — what we decided, or what our pipeline did

| Table | Why it is mutable |
|---|---|
| `ingestion_runs` | A run legitimately moves `RUNNING → SUCCESS / FAILED` |
| `tracked_instruments` | Configuration: which companies we ingest |
| `sources` | A licence note gets corrected on re-verification |
| `line_items` | Reference vocabulary |
| `schema_migrations` | Migration bookkeeping |

**The rule:** if a table describes what a company reported, it is append-only.
If it describes what we decided or what our pipeline did, it is not. That
asymmetry is deliberate and there is a test asserting it, so nobody later
"fixes" it into consistency.

## 3. Three modelling decisions worth defending

### 3.1 Identity is an opaque id, never a ticker

Architecture §2.1. Tickers on Indian exchanges get changed on renames and can
later be reused by a different company. `instruments` is anchored on ISIN,
which is stable per security, and the ticker lives in a versioned table.

`test_a_ticker_alone_cannot_identify_an_instrument` seeds the same ticker
against two different instruments twelve years apart and asserts they stay
distinct. A schema keyed on the string would have merged two companies' history
with nothing looking wrong.

### 3.2 Basis is part of fact identity

Indian companies report both standalone and consolidated figures, and they are
genuinely different numbers. Treating basis as a display preference rather than
part of the key would let the two silently overwrite each other. So it sits in
the natural key alongside period.

### 3.3 Uniqueness is on `(identity, known_from)`, never on value

ADR 004's dedup rule is "do not write a version if the value is unchanged",
which looks like it should be a `UNIQUE` constraint including the value. It
must not be.

A company can restate 1000 → 900 → 1000: a correction later withdrawn. A
constraint including the value rejects the third version and silently loses
the fact that the figure moved back. Since the whole product is the history of
what was believed, losing a version is the worst available outcome.

So the constraint prevents two versions sharing an *instant* — which would make
`DISTINCT ON` pick arbitrarily — and the unchanged-value check lives in the
ingestion path where it can be explicit. `test_a_restatement_that_reverts_to_
an_earlier_value_is_allowed` pins this down.

## 4. The as-of accessors

Invariant 12 says every query is as-of dated. [`006_asof.sql`](../db/migrations/006_asof.sql)
is the one place the predicate is written, so if it is wrong it is wrong once
rather than subtly wrong in twelve call sites.

```sql
SELECT DISTINCT ON (instrument_id, basis, fiscal_year, period_type, line_item)
       ...
FROM   financial_facts
WHERE  known_from <= p_as_of
ORDER  BY instrument_id, basis, fiscal_year, period_type, line_item,
          known_from DESC;
```

Three functions, all `STABLE`:

- `facts_as_of(as_of)` — latest version of every fact as we knew it then.
- `instrument_status_as_of(on, as_of)` — listed or not. **Both axes apply:**
  only events we had *learned* by `as_of`, and among those only ones that had
  taken *effect* by `on`. A delisting announced but not yet effective must not
  remove a company.
- `universe_as_of(on, as_of)` — derived membership: not delisted, and had filed
  something by then.

The default for `as_of` is `now()`. A test asserts that facts stamped ahead of
the clock are excluded by that default, which is the invariant covering a bad
clock or a mis-stamped ingest as well as the future.

## 5. Enforcement, not convention

Invariant 10 is the fragile one: a single `UPDATE` destroys history
irrecoverably, and the history is the product. So it is enforced twice.

**Triggers** ([`005`](../db/migrations/005_append_only.sql),
[`009`](../db/migrations/009_no_truncate.sql)) refuse `UPDATE`, `DELETE` and
`TRUNCATE` on every fact table. These hold even for the table owner, so they
cannot be bypassed by connecting as the wrong role.

**Privileges** ([`007`](../db/migrations/007_roles.sql)) grant `ii_app` only
`SELECT`, and `ii_ingest` `INSERT` but never `UPDATE`. **Neither role has
`DELETE` on anything, anywhere** — nothing in this system has a legitimate
reason to delete a row. A test enumerates
`information_schema.role_table_grants` and asserts the empty set.

### The TRUNCATE hole

Writing the tests found a real gap. The triggers in `005` fire
`BEFORE UPDATE OR DELETE`, and **PostgreSQL does not route `TRUNCATE` through
`DELETE` triggers.** So `TRUNCATE financial_facts` emptied the table with
append-only enforcement fully in place, and reported success.

That is precisely the failure class this project is built against: no error, no
wrong-looking output, history gone permanently. Migration `009` closed it with
statement-level `BEFORE TRUNCATE` triggers.

A follow-on subtlety: plain `TRUNCATE filings` is refused by PostgreSQL's
foreign-key check before the trigger is reached, which makes it easy to think
the FK is the protection. It is not — `TRUNCATE filings CASCADE` satisfies the
FK objection by truncating `financial_facts` too, and only the trigger stops
that. There is a dedicated test for the CASCADE case for exactly this reason.

**The lesson is not about `TRUNCATE`.** It is that an enforcement claim nobody
has attacked is a hope. The exit criterion said "enforced at the database
level", and that claim survived only because it was tested.

## 6. Licence discipline in the schema

`sources` requires a non-empty `licence_note`, a `verified_on` date, and an
explicit `redistributable` flag. Every fact has a `NOT NULL` foreign key to it.

So an adapter whose terms nobody has read has no source row, and without a
source row the foreign key refuses its facts. The Phase 3 rule — no source
adopted on recollection — is enforced by the schema rather than by remembering
to care when in a hurry.

## 7. Honest limitations

- **Index efficiency is unverified at scale.** `EXPLAIN` on the core as-of
  query currently shows a sequential scan, which is the correct plan for a
  near-empty table. Whether `idx_facts_asof` is actually used, and whether
  `DISTINCT ON` stays acceptable across ~500 companies and years of versioned
  facts, cannot be known until Phase 7 loads real volume. Re-run `EXPLAIN
  ANALYZE` then; it is a Phase 19 input if it disappoints.
- **No prices table.** Deliberate, per architecture §2.13. Phase 21 adds it on
  the private path.
- **No user domain.** Phase 13.
- **`universe_as_of` is weaker than index reconstruction.** It answers "was
  this company listed and reporting", not "was it in the NIFTY 500". The UI
  must say so rather than imply index membership.
- **Line-item vocabulary is a guess.** 24 codes covering the v1 metric set.
  Phase 7 will meet filings that do not map onto it, and the vocabulary will
  grow. Valuation items are absent because valuation needs prices.

---

## Exit criteria — met

- [x] Bitemporal schema for instruments, lifecycle, filings, facts, corporate
      actions, and the run log
- [x] Append-only enforced at the database level — triggers **and** privileges,
      including the `TRUNCATE` and `TRUNCATE CASCADE` routes
- [x] **A point-in-time query returns correct results against hand-seeded data
      including a restatement and a delisting** — the criterion, met by
      `test_as_of_before_restatement_returns_the_original_figure` and
      `test_delisted_company_is_present_for_dates_before_it_delisted`
- [x] Migrations idempotent, forward-only, with drift detection
- [x] Schema document explaining each time axis — this file
- [x] 46 tests passing
- [x] Phase 7 may begin
