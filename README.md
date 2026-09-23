# Point-in-time fundamentals screener

*Investment-Intelligence — see
[docs/01-vision-and-scope.md](docs/01-vision-and-scope.md) §8.4 for why the
name took seventeen phases to settle.*

Screen company fundamentals **as of any past date**, using only the figures
that were known on that date.

Every free screener applies today's data to today's universe. When a company
restates a figure they overwrite it, and when a company delists they drop it —
so neither can answer *"what would this screen have returned in March 2023?"*
Both are lookahead bias, and both flatter the result.

This one stores every fact with the date it was learned and never overwrites
anything. A restatement is a new version; a delisting is a date, not a
deletion.

**It does not give investment advice.** No buy/sell calls, no target prices,
no picks. It shows figures companies filed and metrics computed from them, and
every number links to the filing it came from. See
[§3 of the scope document](docs/01-vision-and-scope.md) for why that boundary
is regulatory rather than stylistic.

---

## What it does, concretely

```
SCREEN  net margin >= 10%  AND  ROE >= 12%   |  FY2019, USD
  as of 2020-06-30 — what you would have seen then
     INFY    margin 18.6%   roe 23.4%
     WIT     margin 15.4%   roe 15.8%
     HDB     margin 37.0%   roe 13.5%
     RDY     margin 12.2%   roe 13.4%

PROVENANCE  INFY  NET_MARGIN = 18.6%
     NET_PROFIT   USD    2,200,000,000   FY2019  reported 2019-06-19
        https://www.sec.gov/Archives/edgar/data/1067491/000156459019022837/
     REVENUE      USD   11,799,000,000   FY2019  reported 2019-06-19
        https://www.sec.gov/Archives/edgar/data/1067491/000156459019022837/
```

## Running it locally

Needs **macOS with Homebrew**, **Python 3.12+**, and **Node 22+** (for the web
app only). About five minutes, most of it `npm install`.

```bash
make doctor    # tells you what is missing before you start
make setup     # venv, dependencies, databases, migrations
make seed      # register the data source and the tracked companies
make backfill  # load history from SEC EDGAR — ~10s, hits the network
make test      # the full suite
make web       # http://localhost:3100
```

`make help` lists everything. Every target is safe to run twice.

Set `LC_ALL` if you start PostgreSQL by hand — without it, PostgreSQL 17 on
macOS fails with *"postmaster became multithreaded during startup"*, which
names the symptom and not the cause. `make db-start` does it for you.

### What `make backfill` actually does

Fetches ten years of XBRL financial facts from
[SEC EDGAR](https://www.sec.gov/edgar) for ten companies, one request every
half second. It is polite by design: the SEC permits 10 requests/second and we
take one every 0.5s, with a declared `User-Agent`.

Expect roughly **2,466 facts across 118 filings, 9 of 10 companies, in about
ten seconds.** The tenth is ICICI Bank, which returns 404 from `companyfacts`
and is left in the tracked set on purpose so the gap report has something real
to report.

Re-running is a verified no-op.

## Where the data comes from, and why it is a small set

SEC EDGAR, because its `companyfacts` API is **natively bitemporal**: every
fact carries the period it describes *and* the filing that reported it, with
that filing's date. No other source surveyed has the transaction-time axis at
all — commercial APIs serve the current restated view, so the original figure
is gone the moment a company restates.

The licensing is also unambiguous. From the SEC's own FAQ: *"All
Government-created content on sec.gov and EDGAR public filing content are free
to access and reuse."*

The cost is coverage. Only Indian companies with US listings file with the
SEC, which is about eight of them, and the figures are as filed under IFRS or
US-GAAP rather than the Ind AS numbers filed in India — **so they will not
match Screener.in.** Widening to Indian filings is an open problem, recorded
in [docs/03-data-sources.md](docs/03-data-sources.md) §6.

Publishing Indian market *prices* turns out to cost ₹1,10,000 per display
medium, which is why this screens on fundamentals and shows no prices at all.
That finding, and what it did to the design, is
[§1 of the data sources document](docs/03-data-sources.md).

## How it is built

```
db/migrations/     31 forward-only SQL migrations. No down steps: this is an
                   append-only store and a rollback that drops a column drops
                   facts
src/               Python — ingestion, analytics, accounts, observability
web/               Next.js — read API and site, connects as a SELECT-only role
tests/             the suite, run against a real PostgreSQL
docs/              One document per phase, written before the code
```

Three properties the code is arranged around:

**Nothing is ever updated or deleted.** Enforced by triggers *and* by
privileges, because the triggers cover the table owner and the privileges
cover a dropped trigger.

**Every query is as-of dated.** `facts_as_of(when)` is the only way to read a
fact, and it defaults to now rather than allowing an undated read.

**Ingestion, computation and serving run on different clocks.** No serving
path triggers an external fetch, so traffic scales against our own database
rather than a provider's rate limit.

## Documentation

The phase documents are written before the code and record what was decided
and why — including what turned out to be wrong.

| | |
|---|---|
| [ROADMAP.md](ROADMAP.md) | 22 phases in 4 tiers, with exit criteria |
| [01 Vision and scope](docs/01-vision-and-scope.md) | What this is, what it is not, and the wedge |
| [02 Architecture](docs/02-architecture.md) | 11 components, 12 invariants, 10 failure modes |
| [03 Data sources](docs/03-data-sources.md) | The licensing research that reshaped the product |
| [05 Technology stack](docs/05-technology-stack.md) | Seven ADRs, each naming what it beat |
| [06 Data model](docs/06-data-model.md) | The bitemporal schema |
| [07 First source](docs/07-first-source.md) | EDGAR, and four bugs live data found |
| [08 Incremental ingestion](docs/08-incremental-ingestion.md) | The daily job, and detecting a run that did not happen |
| [09 Analytics](docs/09-analytics-and-screening.md) | Metrics, provenance, point-in-time screening |
| [10–11 API and frontend](docs/10-11-api-and-frontend.md) | The read API and the site |
| [12 Deployment](docs/12-deployment.md) | Neon, Vercel, and why a scheduler cannot watch itself |
| [13 Accounts](docs/13-accounts.md) | Magic-link auth, row level security, alerts |
| [14 Testing strategy](docs/14-testing-strategy.md) | The 24 invisible failures and their tests |
| [15 Security](docs/15-security.md) | Threat model for the actual attack surface |
| [16 Observability](docs/16-observability.md) | Structured logging, status page, operational alerts |
| [17 Data quality](docs/17-data-quality.md) | Accounting-identity checks, and what they found |
| [18 Documentation](docs/18-documentation.md) | Verifying the docs by running them from clean |
| [18 Runbook](docs/18-runbook.md) | What to do when something breaks |
| [API reference](docs/api-reference.md) | The five endpoints |
| [Backlog](docs/backlog.md) | Noticed mid-phase, deliberately not done yet |

## Status

**Tiers 1 and 2 complete except deployment** — phases 1–11 and 13–18. The
platform runs locally end to end: ingestion, metrics, screening, accounts,
alerts, observability, data-quality checks. What it is not is deployed — that
needs a Neon project, a Vercel account and a public repository, and it is
[Phase 12](ROADMAP.md), the one phase left in the first two tiers.

Production readiness landed before deployment on purpose: phase 12 is the only
phase that needs accounts and credentials rather than code, so everything that
could be built without them was.

Known limitations are listed per phase rather than summarised optimistically.
The main ones: no prices, mostly annual periods, nine companies with data out
of ten tracked, and no email is sent because nothing is wired to a provider
yet — so magic-link sign-in and alerts are built but cannot deliver.

## Not to be confused with

[`paper-trader`](https://github.com/Vivek-Reddy7/paper-trader) is a separate,
standalone project: a market data pipeline and backtesting engine. The two
share ideas — idempotent ingestion, validation at the boundary, reconciling
derived state against a log — and are deliberately separate codebases with
separate lifecycles. Neither imports the other.

## Licence

[MIT](LICENSE), which covers the software and not the data.

No third-party market data is redistributed here. Financial facts are fetched
at runtime from SEC EDGAR, whose public filing content the SEC states is *"free
to access and reuse"* ([webmaster FAQ](https://www.sec.gov/os/webmaster-faq)).
Indian exchange prices are absent entirely, and
[docs/03-data-sources.md](docs/03-data-sources.md) explains why that was a
licensing decision rather than an oversight.
