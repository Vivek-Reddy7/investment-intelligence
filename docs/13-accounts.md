# Phase 13 — Accounts and Personalisation

Status: **data layer complete; email sending and UI need deploy keys**
Last updated: 2026-09-11

258 tests. Sign-in, watchlists, saved screens, portfolio, and edge-triggered
alerts — with isolation enforced by the database rather than by our queries.

---

## 1. Two things enforced below the application

### 1.1 Row level security, because a leak here is silent

One user reading another's watchlist or holdings is the worst failure this
phase can produce, and it produces **no error**: the page renders, with
someone else's portfolio on it.

Enforcing that in application queries means every future query is one
forgotten `WHERE` clause away from the leak. So it is enforced by PostgreSQL
row level security, keyed on a session variable the web app sets per request.

The queries in `accounts/store.py` contain **no `user_id` predicate at all**.
That is deliberate: the same mistake that would have leaked everything now
returns nothing, which is visible immediately.

Policies are `ENABLE`d *and* `FORCE`d. Enabled alone exempts the table owner,
and on a managed database the owner is often the role the application connects
as.

### 1.2 A separate schema, because invariant 8 should be a privilege

User data lives in `app`, market data in `public`, and `ii_ingest` is granted
**nothing** on `app`. So an ingestion job cannot join against a watchlist even
by accident — invariant 8 stops being a promise. Alert evaluation therefore
needed its own role, `ii_alerts`, rather than reusing the ingestion one.

There is also no foreign key from `public` into `app` in either direction, and
`watchlist_items.instrument_id` is deliberately **not** a foreign key. It is
the only place in the project where a missing FK is the right call: one would
make the two domains mutually dependent, so rebuilding the market schema would
cascade into user rows. Referential integrity is checked in the application,
explicitly, which is the price of keeping them separable.

## 2. No passwords, anywhere

See [ADR 007](05-technology-stack.md#adr-007--authentication-by-emailed-magic-link).
Sign-in is a single-use emailed link. There is no password column, no hashing,
no comparison, no transport — a test asserts the schema has no such column, so
adding one later has to fail a test first.

Three rules in `accounts/auth.py`:

- **Only hashes are stored**, and tokens are looked up *by* hash, so no
  candidate value is ever compared in application code and there is no timing
  signal from a comparison loop.
- **Every failure returns the same thing.** Unknown, expired and
  already-consumed are indistinguishable. Distinguishing them turns the
  sign-in form into an account-enumeration oracle, and a test asserts the two
  error messages are byte-identical.
- **Beginning a login reveals nothing** and creates no account. The account is
  created on successful consumption, so submitting an address tells you
  nothing about whether it is registered.

## 3. Alerts are edge-triggered

"ROE is below 15%" is true every day once it becomes true. An evaluator that
delivers whenever the condition holds sends the same message daily until the
user disables everything.

What a reader wants is the transition:

```
deliver when (condition is true) AND NOT (it was true last time)
```

`app.alert_state` remembers the last verdict per (rule, instrument). A rule
that flips back to false **re-arms**, so a metric genuinely oscillating around
a threshold alerts each time it crosses — suppressing that would be as wrong
as repeating while true. There are tests for the transition, the silence
while true, and the re-arm.

**Firing and sending are separate steps.** `evaluate()` records a delivery;
a sender picks up `pending_deliveries()`. If firing also sent the email, a
failing provider would either lose the alert or stall the evaluation cycle
(architecture §2.10).

`instrument_id` null means "any company in the universe", which is how a
saved screen becomes an alert. Rules are also filtered to companies currently
in the universe, because an alert on a delisted company is noise.

## 4. Three bugs the tests found

### 4.1 The isolation tests were proving nothing

They passed before `ii_app` was involved — and they would have passed **with
no policies at all**. PostgreSQL exempts superusers from row level security
unconditionally, and the harness connects as the database owner, which on a
local install is a superuser.

Fixed with an `app_role` helper that runs the assertions as `ii_app`, plus a
test that asserts the superuser bypass is real so the role-setting cannot
later be removed as ceremony.

This is the same shape as the Phase 6 `TRUNCATE` hole: an enforcement claim
nobody has attacked from the right angle is a hope.

### 4.2 `CREATE ROLE` is not idempotent, and roles are cluster-wide

Migration 007 guarded `CREATE ROLE` against `pg_roles`; migration 020 did not,
and the harness — which rebuilds the schema around every committing test —
failed on it immediately. There is now a test asserting every `CREATE ROLE` in
any migration carries the guard.

Separately, the harness's own `_reset_schema` dropped `public` but not `app`,
so re-running 020 hit a surviving schema. **One incomplete reset produced 94
cascading errors.** Roles are still not dropped, deliberately: they are
cluster-wide, so dropping them could break another database in the same
cluster.

### 4.3 RLS raises rather than returning zero rows

Inserting into someone else's watchlist triggers a `WITH CHECK` violation,
which raises and **aborts the transaction** — I had assumed it would simply
affect zero rows.

Raising is the better behaviour. But without a savepoint, one refused insert
would poison every later statement in the request, so `add_to_watchlist` now
wraps it and converts the error to `NotFound` — the same response as a
nonexistent id, which is what stops id enumeration.

## 5. What the portfolio deliberately does not do

No market value, no profit and loss, no custody, no KYC. Prices are behind the
private path (Phase 3), so there is nothing to mark a holding to. It stores
quantity and cost basis without pretending to value them, which is the honest
version of a portfolio on a platform with no price data.

## 6. Limitations

- **No email is sent.** The flow issues and consumes tokens correctly; nothing
  is wired to a provider because that needs an account key. `LoginLink` is
  returned to the caller, which is fine for tests and not a sign-in
  experience.
- **No UI.** The data layer and its tests are complete; the pages are not
  built. Deliberate: `/api/v1` and the screener are unchanged and still work
  signed-out, and wiring an auth UI before there is an email sender would be
  building against a stub.
- **Alert evaluation is not scheduled.** `evaluate()` is a function; nothing
  calls it on a timer yet. It belongs in the daily workflow after the metric
  refresh, which is a Phase 12 wiring task.
- **No rate limiting on login.** Anyone can request unlimited magic links for
  any address, which is an email-bombing vector. Phase 15.
- **Session revocation is per-session.** No "sign out everywhere".

---

## Exit criteria

- [x] Email authentication — magic link, no passwords
- [x] Watchlists, saved screens, manually entered portfolio
- [x] Alerts on user-defined conditions, edge-triggered
- [x] **Market data still has no dependency on the user domain** — no FK, and
      `ii_ingest` has no grant on `app`
- [x] Cross-user isolation enforced by the database, verified under the
      application role
- [ ] *A user can sign in and receive an alert* — the mechanics are tested end
      to end, but no email leaves the machine until Phase 12 supplies a sender
