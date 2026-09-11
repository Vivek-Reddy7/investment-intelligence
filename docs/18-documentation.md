# Phase 18 — Documentation

Status: **complete, verified by doing it**
Last updated: 2026-09-11

Documentation has been continuous since Phase 1. This phase is the checkpoint
where it was **verified rather than assumed**.

---

## 1. The exit criterion, actually performed

> Someone who has never seen the project runs it locally using only the docs.

Not asserted — done. The venv, `node_modules`, `web/.env.local`, both
databases and the `ii_web` role were all deleted, and then the README was
followed verbatim:

```
make doctor    →  postgres 17.11, python 3.14.6, node v22.18.0, venv MISSING
make setup     →  23 migrations applied
make seed      →  10 companies, source registered, schedule declared
make backfill  →  2,466 facts, 118 filings, 9 of 10 companies, 8.5s
make test      →  359 passed
make status    →  OVERALL: OK
make web       →  screener, /status and /api/v1/health all 200
```

The numbers the README quotes are the numbers that came out.

## 2. What running it from clean actually found

**A missing step nobody would have guessed.** The web app needs a database
role, `ii_web`, that does not exist until someone creates it — and it cannot
live in a migration, because it needs a password and a password in a migration
is a password in git. It was created by hand in Phase 10 and never written
down. A stranger would have hit an authentication failure with no instruction
anywhere. `make web-role` now does it.

**A confusing failure with a misleading symptom.** A dev server left running
from an earlier session keeps serving from its own build, so after wiping
`node_modules` the site returned 500s on every route while the *real* error —
`EADDRINUSE` — sat further up the scrollback. It looked like an application
bug for several minutes. `make web` now refuses to start and says what to do.

**A wrong workspace root.** Next was walking up the filesystem for a lockfile
and settling on `~/package-lock.json`, which belongs to something else
entirely. That changes which files are traced into a production bundle, so it
is a correctness issue rather than only a warning. Now pinned with
`outputFileTracingRoot`.

**The locale gotcha, written down at last.** PostgreSQL 17 on macOS refuses to
start without `LC_ALL`, failing with *"postmaster became multithreaded during
startup"* — a message that names the symptom and not the cause. It cost time
once in Phase 6 and was never recorded. It is now in the Makefile and the
README, and a test asserts both.

## 3. A Makefile, so the docs cannot drift

Commands live in one place and the README describes them. A document that
spells out `PYTHONPATH=src python -m investment_intelligence.cli backfill
--since 2015-01-01` is stale the first time an argument changes; `make
backfill` is not.

Every target is safe to run twice, so there is no ordering a reader can get
wrong badly enough to need a reset. `make doctor` reports what is missing
*before* anything is attempted, because a clear "you have no Node" beats a
stack trace from `npm`.

## 4. Documentation that cannot rot quietly

`tests/test_documentation.py` — 40 tests. They cannot judge whether prose is
good; they check the things that decay silently:

- **Every internal link resolves.** The commonest rot, and the first thing a
  reader hits.
- **Every phase document is linked from somewhere.** Five were orphaned,
  including this project's own accounts and observability write-ups. A
  document nobody links to is a document nobody reads.
- **Every command the README offers exists** as a Makefile target.
- **Every API route is documented** — derived from the filesystem, so a new
  route with no reference entry fails.
- **Every health status and rejection class has a runbook entry** — derived
  from the code, so a new one with no guidance fails.
- **Counts in prose match reality**, for migrations and tests.
- **The README no longer says "Nothing is built yet"**, which it did for
  sixteen phases and was the single most misleading sentence the project has
  had.
- **No document claims the data is annual-only** unless the claim is marked
  superseded — it was true, Phase 7's fix made it false, and it survived in
  two places.
- **Every file the README links to is tracked by git.** This is exactly how
  `.env.local.example` stayed invisible until Phase 15: present locally, never
  committed, so a clean checkout was missing it for everyone but its author.

Two of these failed on their first run and both were real: five orphaned
documents, and a test-count check that failed because writing it had just
changed the count it was checking.

The second was removed rather than fixed, and §5 explains why.

## 5. Two things these tests taught me about testing documentation

**Do not assert on unwrapped prose.** The first version failed on
`will not match Screener.in`, because markdown wraps and the phrase spanned a
line break — which says nothing about whether the claim is present.
Assertions now normalise whitespace first. The brittle version would have gone
red every time a paragraph reflowed, which is how a useful check becomes one
people delete.

**Do not assert a number that is allowed to move.** A test checking the
README's test count failed on its first run, because writing it added two
tests and changed the count. That is not a one-off: such a test goes red on
every legitimate change, teaching people that red means "update the number"
rather than "read the failure" — precisely the flakiness Phase 14 called worse
than a missing test.

It was removed, and the README no longer quotes an exact count. The migration
count check stays, and the difference is the point: **the property that makes
a number in prose safe to assert is how rarely it is allowed to move.**
Migrations change deliberately and seldom; tests change constantly and should.

## 6. Deliverables

| | |
|---|---|
| [README](../README.md) | Rewritten. The entry point, verified by execution |
| [Makefile](../Makefile) | The commands, so docs and reality cannot diverge |
| [18 Runbook](18-runbook.md) | Organised by *what you saw*, not by component |
| [API reference](api-reference.md) | Five endpoints, with what is deliberately absent |
| `tests/test_documentation.py` | 40 tests against doc rot |

The runbook is organised by symptom on purpose. Organising by component is
what you want once you already know the answer; *what you saw* is what you
have when you arrive.

## 7. Limitations

- **Verified on one machine.** macOS with Homebrew. A Linux reader will find
  `PG_BIN` and the `sed -i ''` in the Makefile are wrong for them, and
  nobody has tried it.
- **No deployment documentation**, because there is no deployment. The runbook
  says so rather than leaving the gap implicit.
- **No backup or restore procedure.** Market data is reproducible from EDGAR;
  user data has no answer yet, and that is stated in the runbook rather than
  omitted.
- **The prose is unreviewed by anyone else.** "A stranger can follow it" was
  tested by removing everything and following it — which is the closest
  available proxy and is not the same as an actual stranger.

---

## Exit criteria

- [x] Architecture docs kept current — no document contradicts the code, and
      superseded claims are marked rather than deleted
- [x] A local setup guide — the README, verified from clean
- [x] Operational runbooks — by symptom, covering every health status and
      rejection class the code can produce
- [x] An API reference — every route, derived from the filesystem
- [x] **A stranger can run it locally from the README alone** — performed, §1
