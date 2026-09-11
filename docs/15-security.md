# Phase 15 — Security Hardening

Status: **complete**
Last updated: 2026-09-11

293 tests. A threat model for the actual attack surface, rate limiting on the
two vectors that exist, a privilege audit that enumerates everything, and one
critical CVE fixed.

---

## 1. The actual attack surface

Generic checklists are not useful here, so this is what the system genuinely
exposes:

| Surface | Authentication | Notes |
|---|---|---|
| `GET /api/v1/{meta,screen,company,explain}` | **None** | Public by design. All data is public filings |
| `GET /api/v1/health` | None | Returns 503 when stale; no detail beyond job names |
| `POST` sign-in (planned) | None | Accepts an arbitrary email address |
| Session-scoped user data | Session cookie | Watchlists, portfolio, saved screens, alerts |
| GitHub Actions ingestion | Repository secret | Holds `DATABASE_URL` |

Notably **not** exposed: no file upload, no order placement, no payment, no
admin surface, no user-generated content rendered to other users, and no
password anywhere.

## 2. Threats and what stops them

| # | Threat | Mitigation | Where |
|---|---|---|---|
| 1 | One user reads another's portfolio | Row level security, `ENABLE`d and `FORCE`d, policies keyed on a per-request session variable. The store's queries contain **no `user_id` predicate** — RLS is the filter, so a forgotten clause returns nothing rather than everything | mig. 020 |
| 2 | A read-path bug corrupts market data | `ii_app` holds `SELECT` only on `public`. Verified by an audit that enumerates every grant | mig. 007, `test_security` |
| 3 | History is rewritten | Append-only triggers on `UPDATE`/`DELETE`/`TRUNCATE`, and no application role granted either. Enforced twice because the triggers cover the owner and the privileges cover a dropped trigger | mig. 005/009 |
| 4 | Ingestion reads user data | Separate schema; `ii_ingest` granted **nothing** on `app`. Invariant 8 as a privilege | mig. 020 |
| 5 | SQL injection via screen criteria | Operators from an allow-list in both Python and TypeScript; thresholds regex-validated as numeric; metric codes checked against the `metrics` table. Everything else parameterised | `screen.py`, `queries.ts` |
| 6 | **Login email flooding at a third party** | Rate limited per address (5/hour) *and* per client (10/hour) | mig. 021 |
| 7 | **DoS via expensive historical screens** | Rate limited 60 per 10 minutes per client. See §3 | mig. 021 |
| 8 | Account enumeration via sign-in | Every failure returns an identical message; `begin_login` neither checks nor creates an account. A test asserts the expired and unknown messages are byte-identical | `auth.py` |
| 9 | Session theft via XSS | `httpOnly` cookie, plus a CSP with no `unsafe-inline` for scripts and no third-party script origin at all | `session.ts`, `next.config.mjs` |
| 10 | CSRF | `sameSite=lax`, and no state-changing `GET` endpoint | `session.ts` |
| 11 | Stolen database yields sessions | Only SHA-256 digests stored, looked up *by* digest so no candidate value reaches application code | `auth.py` |
| 12 | Timing attack on token comparison | Lookup by hash inside an index; no byte-wise comparison in Python | `auth.py` |
| 13 | Credential committed to git | Pattern scan over tracked files, blocking in CI | `test_security` |
| 14 | Clickjacking | `frame-ancestors 'none'` plus `X-Frame-Options: DENY` | `next.config.mjs` |
| 15 | Referrer leakage to sec.gov | `strict-origin-when-cross-origin` — provenance links must not carry a screen's criteria to a third party | `next.config.mjs` |
| 16 | Rate-limit table becomes a visitor log | Subjects stored as SHA-256 digests | mig. 021 |

## 3. The DoS vector that is ours alone

Worth its own section because nothing generic would have found it.

A **live** screen reads the materialised `metric_values` table. A
**historical** screen calls `metrics_as_of(date)`, which recomputes the entire
metric pivot across every versioned fact.

That path is unauthenticated, it is the headline feature, and the as-of date
is a free parameter. So an attacker gets an unlimited number of **distinct,
uncacheable, full-table computations** from a query string — and on a free tier
with 100 compute-hours a month, that is a cheap outage. Nothing about the
requests looks abnormal in a log: they are exactly what the product is for.

Mitigated by rate limiting the historical path specifically, at a level a
person exploring dates will never notice.

## 4. One critical CVE, found by adding the audit

`npm audit` on first run reported **1 critical and 2 high**. The critical was
`GHSA-9qr9-h5gf-34mp` — remote code execution in Next.js's React flight
protocol — plus around thirty other advisories against Next 15.5.4.

Fixed by a non-breaking bump to **15.5.25**. Build and typecheck unaffected.

### 4.1 Three advisories deliberately not fixed

What remains needs Next **16**, a major version. Rather than take that
reflexively, each was checked against what this app actually does:

| Advisory | Requires | Present here? |
|---|---|---|
| `postcss` XSS + arbitrary `.map` read via `sourceMappingURL` | Attacker-influenced CSS processed at build | **No.** No `postcss.config`, and the only CSS is a static file we wrote. Build-time, not runtime |
| `sharp` libvips/libheif CVEs | Image processing | **No.** Zero `next/image` uses; `sharp` is transitive and never invoked |
| `next` moderate | Server Actions / middleware / rewrites / i18n | **No.** Zero `use server`, no `middleware.ts`, no rewrites, no i18n |

So all three target code paths that do not exist in this application. Deferred
with that reasoning recorded, and revisited when Next 16 is taken for its own
reasons.

### 4.2 Why the audit job does not block

`continue-on-error: true` on both audits, and that is a judgement rather than
laziness. A new advisory against a transitive dependency can appear overnight
with no fix available, and a build that goes red for something nobody can act
on gets ignored — which is worse than one that reports. A blocking gate belongs
here once someone is on call to unblock it.

**The secret scan does block.** A committed credential is always actionable,
and always by the person who just pushed it.

## 5. The privilege audit

Earlier phases tested individual claims: `ii_app` cannot insert facts, no role
can delete one. Necessary, and not sufficient — they check the grants somebody
thought to check.

`test_no_role_holds_a_privilege_nobody_decided_on` enumerates **every** grant
held by every application role and compares it against a written expected set.
A migration that over-grants now fails even if nobody wrote a test for that
table. It caught one omission immediately (`ingestion_schedule`), which was
legitimate and simply unlisted.

Also asserted: no application role is a superuser, none can bypass RLS, and
none can create objects in `public` — an application role that can `DROP` a
trigger can defeat the append-only guarantee.

## 6. Two things the secret scan taught me

**A committed password, and a file nobody could see.** The example environment
file was named `.env.local.example`, which `.gitignore`'s `.env.*` glob
swallowed — so it was **never tracked**, and a new developer could not discover
which variables the app needs. It also carried a real (if localhost-only)
password.

Both wrong for different reasons. Now `web/env.example`, tracked, with a
`REPLACE_ME` placeholder and a line showing how to generate a real one.

**A scanner that flags its own test data.** The scan then caught the example
credentials inside its own test file. The easy fix would have been to exclude
that file, which leaves a hole precisely where someone might hide something.
Instead the test URLs are assembled from parts, so no literal credential
exists anywhere in the repository.

The placeholder exemption is narrow on purpose: all-caps-with-underscores in
the password position, a shape a real password essentially never takes. Seven
parameterised cases assert it still flags `devonly`, `hunter2`, `Tr0ub4dor3`
and `ABCdef123`.

## 7. Accepted risks

Stated rather than omitted.

- **Fact and instrument ids are enumerable** via `explain?facts=` and
  `company/:id`. Accepted: every fact is public filing data, which is the
  product. Worth revisiting only if non-public data is ever added.
- **No 2FA.** A magic link is already possession-of-inbox, so a second factor
  would mostly be theatre at this stage. Reconsider before Phase 21 connects
  broker accounts.
- **No "sign out everywhere".** Revocation is per-session. Should exist before
  broker credentials do.
- **Rate limiting by address is a mild lockout vector.** An attacker can
  exhaust a victim's quota for requesting a *new* link. It does not sign them
  out and does not invalidate a link they hold. A brief inability to request an
  email is a smaller harm than letting anyone flood an inbox, but the trade is
  real and was chosen.
- **`x-forwarded-for` is trusted** for the client identifier. Sound behind
  Vercel, which sets it; the first entry is taken because later ones are
  attacker-appendable. Would be wrong on a host that does not set it.
- **No WAF, no bot detection, no DDoS protection** beyond what the platform
  provides.
- **The database owner is a superuser locally.** Superusers bypass RLS
  unconditionally, which is why tests assert under `ii_app`. In production the
  application role must not own the schema.

---

## Exit criteria

- [x] **No secrets in the repository** — pattern scan over tracked files,
      blocking in CI; one committed password removed
- [x] **Application role cannot mutate history** — triggers and privileges,
      verified by an audit that enumerates every grant
- [x] **Dependency scanning in CI** — `pip-audit` and `npm audit`; found and
      fixed a critical RCE
- [x] A threat model for the actual attack surface — §1, §2
- [x] Rate limiting on both real vectors
- [x] Security headers served and verified
- [x] Least-privilege roles, asserted exhaustively
