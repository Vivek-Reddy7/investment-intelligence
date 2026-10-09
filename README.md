# Point-in-time fundamentals screener

*Investment-Intelligence — see
[docs/01-vision-and-scope.md](docs/01-vision-and-scope.md) §8.4 for why the
name took seventeen phases to settle.*

[![ci](https://github.com/Vivek-Reddy7/investment-intelligence/actions/workflows/ci.yml/badge.svg)](https://github.com/Vivek-Reddy7/investment-intelligence/actions/workflows/ci.yml)
[![licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)

Screen company fundamentals **as of any past date**, using only the figures
that were known on that date.

The free screeners we surveyed apply today's data to today's universe. When a
company restates a figure they overwrite it, and when a company delists they drop it —
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

## How this compares

Point-in-time fundamentals are not a new idea: institutions have paid for them
for decades. What is scarce is an open, auditable implementation on free public
data.

| Alternative | What it is | Where it is strong | Why it does not answer the same question |
|---|---|---|---|
| Free Indian screeners (Screener.in and similar) | Web screeners over current financials | Broad Indian coverage, polished, free | They show the current, restated view, so you cannot ask what a screen returned on a past date ([docs/01 §2.1](docs/01-vision-and-scope.md)) |
| [S&P Compustat Point-in-Time](https://www.spglobal.com/market-intelligence/en/solutions/products/fundamental-data) | Commercial database that preserves each restatement; S&P's own pages put its snapshots as far back as 1987 | Institutional depth and coverage | A licensed product with closed data and schema, not something an individual can audit or run |
| [Sharadar Core US Fundamentals (SF1)](https://data.nasdaq.com/databases/SF1) | Paid dataset on Nasdaq Data Link with as-reported dimensions keyed to the filing date | Clean as-reported series for US companies | A purchased dataset rather than a pipeline you can inspect; US companies only |
| [pit-fundamentals](https://github.com/christianpichichero-max/pit-fundamentals) | Small CC0 CSV of US fundamentals from SEC EDGAR, each value stamped with a `first_filed` date | The closest open neighbour: same source, same idea | A static data pack (its README states 5 companies, annual filings), not a store with enforced history or a screening layer |
| [edgartools](https://github.com/dgunning/edgartools) | MIT-licensed Python library for reading EDGAR filings and XBRL | Excellent parsing of the same source this project uses | A library for reading filings, not a versioned store with a screening layer |

**What this adds:** the integrity guarantees live in the database rather than
in convention (nothing is updated or deleted, and there is no undated read), it
runs on free public data, and every number links to the filing it came from.

**What it does not add:** coverage, a data licence, or a moat. A well-funded
team could rebuild the idea quickly. The value here is the implementation and
the decisions behind it, which are written down. The
[project analysis](docs/project-analysis.md) rates the novelty of the idea
**Low** and sets out the strongest criticisms.

## Design decisions worth reading

| Decision | Why | Where |
|---|---|---|
| Append-only, enforced by triggers **and** privileges | One `UPDATE` from a future version of us would destroy history irrecoverably; triggers cover the table owner, privileges cover a dropped trigger | [docs/06](docs/06-data-model.md), migration 007 |
| Every fact carries its period and the filing date it was learned | Makes "as of" a property of the data instead of a filter someone can forget | [docs/06](docs/06-data-model.md) |
| One read path, `facts_as_of(when)`, defaulting to now | An undated read is how lookahead bias gets in | [docs/09](docs/09-analytics-and-screening.md) |
| Ingestion, computation and serving run on different clocks | No page load can trigger an external fetch, so traffic cannot exhaust a provider's rate limit | [docs/02](docs/02-architecture.md) |
| Prices are local-only by design | Exchange and Yahoo licensing; the restriction is encoded as `redistributable = false` on the source row, not left as a comment | [docs/03 §7](docs/03-data-sources.md) |
| Pooled connections for the web app, direct for ingestion; migrations run from the ingest job, not on deploy | Transaction pooling breaks server-side prepared statements, and migrations should run where an operator can read the output | [docs/12](docs/12-deployment.md) |
| Tests run against a real PostgreSQL; CI enforces an 80% coverage floor | The failures that matter here (lookahead, a silent overwrite) are invisible in mocks | [docs/14](docs/14-testing-strategy.md) |

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
that filing's date. No other free source surveyed has the transaction-time axis
at all — most commercial APIs serve the current restated view, so the original
figure is gone the moment a company restates. (Institutional point-in-time
products such as Compustat do keep it, and are licensed; see
[How this compares](#how-this-compares).)

The licensing is also unambiguous. From the SEC's own FAQ: *"All
Government-created content on sec.gov and EDGAR public filing content are free
to access and reuse."*

The cost is coverage. Only Indian companies with US listings file with the
SEC, which is about eight of them, and the figures are as filed under IFRS or
US-GAAP rather than the Ind AS numbers filed in India — **so they will not
match Screener.in.** Widening to Indian filings is an open problem, recorded
in [docs/03-data-sources.md](docs/03-data-sources.md) §6.

Publishing Indian exchange *prices* is licensed per display medium, which is
why the public site screens on fundamentals and shows no prices. The NSE
end-of-day tariff the design was based on put a website at ₹1,10,000, with a
mobile app as a second, separate charge. NSE's
[price list effective 1 April 2026](https://nsearchives.nseindia.com/web/mediaattachment/2026-03/NSE_Pricing_file_-_Domestic_clients_20260309171343.pdf)
shows end-of-day at ₹1,00,000 per medium per year, 15-minute-delayed at
₹1,20,000, and real-time snapshots at ₹23–29 lakh. Figures differ between NSE
documents and change over time, so check the current one. That finding, and
what it did to the design, is
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

## Local research layer (not published)

On top of the point-in-time store sits a research layer for private use. It
works on price history for the tracked US-listed companies, fetched with
`yfinance` and stored under a source row flagged `redistributable = false`
([docs/03 §7](docs/03-data-sources.md)). It is deliberately **not wired into the
public site or API**, because Yahoo's terms restrict redistribution.

| Piece | What it does | Target |
|---|---|---|
| Factor score | Rank-combined momentum, margin and revenue growth, point-in-time correct | `make factors` |
| Technical indicators | SMA and crossovers, RSI, MACD, Bollinger bands, breakouts | `make technicals` |
| Combined score and sizing | Blends the two into one rank; splits an amount across the top names | `make combined`, `make size AMOUNT=…` |
| Risk | Volatility, maximum drawdown, historical VaR | `make risk` |
| Validation | Checks whether a higher combined-score rank correlates with better forward returns: an information-coefficient study, no trading | `make backtest` |
| Local report | One dependency-free HTML file with inline SVG price charts | `make factor-report` |
| Strategy backtest | Runs [`paper-trader`](https://github.com/Vivek-Reddy7/paper-trader)'s backtest over the top-ranked basket by calling its CLI as a subprocess | `make basket-backtest` |
| Run log | Records whether the pipeline actually ran, and when | `make analytics-status` |

**What this is not.** It is a research tool that stays on the author's machine,
not a recommendation service, and the public site contains none of it. With
nine companies, none of its output is statistical evidence of an edge. It exists
to prove the pipeline end to end and to make the lookahead question concrete.

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
| [Project analysis](docs/project-analysis.md) | Novelty rating, build-vs-contribute, a harsh stress test, and a staged roadmap with gate questions |
| [API reference](docs/api-reference.md) | The five endpoints |
| [Backlog](docs/backlog.md) | Noticed mid-phase, deliberately not done yet |

## Status

**Tiers 1 and 2 complete except deployment** — phases 1–11 and 13–18. The
platform runs locally end to end: ingestion, metrics, screening, accounts,
alerts, observability, data-quality checks, plus the local research layer
above.

The repository is public. Every push runs [`ci`](.github/workflows/ci.yml):
Python tests against a real PostgreSQL with an 80% coverage floor, a
dependency audit, the web app, and an API contract check. The nightly
[`ingest`](.github/workflows/ingest.yml) workflow runs on schedule, but until
deployment is wired there is no `DATABASE_URL` secret, so it exits with a
notice instead of failing. **A green ingest run today means "skipped", not
"ingested".**

What is not done is [Phase 12](ROADMAP.md), the one phase left in the first
two tiers: a Neon project, a Vercel account and an uptime monitor. It needs
accounts and credentials rather than code, so everything that could be built
without them was built first. The design is in
[docs/12-deployment.md](docs/12-deployment.md).

## Limitations, stated plainly

- **Coverage.** Ten tracked companies, nine with data. Only Indian companies
  with US listings file with the SEC, and the figures are as filed under IFRS
  or US-GAAP, so they will not match Screener.in.
- **Periods.** Mostly annual.
- **No public prices.** Licensing, not engineering. The local price layer is
  for research only.
- **Restatements only appear when a company files one.** A company that never
  restates gives the point-in-time machinery nothing to show.
- **Small sample.** Nine companies cannot validate a factor or a strategy; the
  research layer proves the pipeline, not an edge.
- **No email.** Nothing is wired to a provider, so magic-link sign-in and alerts
  are built but cannot deliver.
- **Not deployed**, and maintained by one person.
- **No moat.** See [How this compares](#how-this-compares).

Known limitations are also listed per phase in the docs rather than summarised
optimistically.

## Roadmap

1. **Phase 12 — free-tier deployment:** Neon Postgres, Vercel, GitHub Actions
   cron, and an uptime monitor on `/api/v1/health`. Exit criteria are in
   [ROADMAP.md](ROADMAP.md).
2. **Tier 3:** performance engineering with before-and-after benchmarks
   (phase 19), a rehearsed scale path past the free-tier ceilings (20), a
   private per-user broker path, the only legal way to offer live and personal
   data (21), and deferred expansion decided one item at a time (22).
3. **Open data problems**, in [docs/backlog.md](docs/backlog.md): corporate
   actions, an exchange holiday calendar, a share-count source for MakeMyTrip,
   and widening to Indian filings.

## Not to be confused with

[`paper-trader`](https://github.com/Vivek-Reddy7/paper-trader) is a separate,
standalone project: a market data pipeline and backtesting engine. The two
share ideas — idempotent ingestion, validation at the boundary, reconciling
derived state against a log — and are deliberately separate codebases with
separate lifecycles. Neither imports the other, including
`scripts/local_basket_backtest.py`, which runs paper-trader's strategy
backtest over this project's top-ranked basket by calling its CLI as a
subprocess and parsing its printed report — the same interface anyone
running paper-trader by hand already reads, not a shared library or a
shared store. See that script's module docstring for why.

## Licence

[MIT](LICENSE), which covers the software and not the data.

No third-party market data is redistributed here. Financial facts are fetched
at runtime from SEC EDGAR, whose public filing content the SEC states is *"free
to access and reuse"* ([webmaster FAQ](https://www.sec.gov/os/webmaster-faq)).
Indian exchange prices are absent entirely, and
[docs/03-data-sources.md](docs/03-data-sources.md) explains why that was a
licensing decision rather than an oversight.
