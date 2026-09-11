# Phase 1 — Vision and Scope

Status: **draft, awaiting sign-off**
Last updated: 2026-09-11

This document fixes what we are building and, more importantly, what we are
not. Every later phase is allowed to refer back to this one and refuse work
that falls outside it. If something here turns out to be wrong, we change it
here first and then change the code.

---

## 1. The problem

A retail investor in India can find a price anywhere. What is hard to find is
context: how a company's numbers have moved over several years, how it sits
against its actual peers on a consistent set of measures, and where a figure
came from.

The data is public. It is scattered across exchange filings, annual reports,
broker terminals that require a login, and screener sites that either paywall
the useful parts or show a computed ratio without showing the inputs. Someone
who wants to do their own analysis spends most of their time collecting and
reconciling data rather than thinking about it.

## 2. What this is

A publicly accessible web platform for exploring and analysing Indian listed
equities.

It ingests end-of-day market data and company fundamentals, stores them in a
queryable form, and exposes screening, peer comparison, charting and derived
metrics on top. Anyone can browse without an account. Signed-in users get
watchlists, a simulated portfolio and alerts.

The guiding principle for every feature: **show the number and show how it was
derived.** A P/E on this platform should be clickable down to the earnings
figure, the period it covers, and the date it was ingested.

## 3. What this is NOT

These are hard boundaries, not preferences. They constrain the architecture,
so they belong in Phase 1 rather than being discovered in Phase 8.

**Not a recommendation service.** No buy/sell/hold calls, no target prices, no
"top picks this week". SEBI's Research Analyst regulations govern public
investment advice, and we are not registered. The platform presents data and
computed metrics and lets the user draw their own conclusion. Practical test
for any proposed feature: *can it be stated as a calculation rather than an
opinion?* "Companies with ROCE above 20% and debt-to-equity below 0.5" is a
filter. "Best stocks to buy now" is advice. We ship the first and never the
second.

**Not a redistributor of licensed market data.** Broker APIs (Kite Connect,
Angel One SmartAPI and similar) license data for the authenticated user's own
consumption. Republishing that feed on a public website is a data-vending
activity governed by exchange policy, and doing it without an agreement is a
straightforward violation. This single constraint is the main thing shaping
the data architecture, and Section 6 covers how we work within it.

**Not a live-tick terminal.** The public surface is end-of-day and delayed
data. Real-time streaming is both a licensing problem and an infrastructure
cost problem, and it is not where the value of this platform is.

**Not a broker.** No order placement, no funds movement, no KYC, no custody.
Portfolio tracking is bookkeeping the user enters or imports, nothing more.

**Not a backtesting engine.** That is `paper-trader`, a separate project. If
this platform ever needs backtesting, it consumes that as a library or a
service rather than growing a second copy of it.

## 4. Users

**Primary — the self-directed retail investor.** Comfortable reading a balance
sheet, wants to screen and compare rather than be told what to do. Today they
use some combination of a broker app, a free screener, and a spreadsheet.
Their complaint is that the spreadsheet is manual and the screener is opaque.

**Secondary — the learner.** A student or early-career engineer who wants to
understand how market data fits together. The "show the derivation" principle
serves this user directly, and they cost us almost nothing to support because
they use the same read paths.

**Explicit non-user — the active trader.** Someone who needs live ticks,
depth, and sub-second latency. We cannot serve them legally on a free tier and
should not distort the design trying.

## 5. Scope of v1

v1 is the smallest thing that is genuinely useful and genuinely demonstrates
the engineering. Everything here should be reachable on free infrastructure.

**In scope**

| Area | v1 |
|---|---|
| Coverage | Indian listed equities, NSE-listed universe |
| Market data | Daily OHLCV, adjusted for splits and bonuses |
| Fundamentals | Annual and quarterly: revenue, profit, margins, debt, cash flow |
| Derived metrics | Valuation, profitability, leverage, growth ratios, each traceable to its inputs |
| Screening | Multi-criteria filter over the metric set, saveable |
| Company page | Price history, financial history, peer comparison |
| Accounts | Email sign-in, watchlists, manually entered portfolio, price and metric alerts |
| Freshness | Daily ingestion after market close, with visible last-updated stamps and gap detection |

**Explicitly deferred**

Derivatives and options. Mutual funds. Non-Indian markets. Intraday and
real-time data. News and sentiment. Anything ML-driven. Mobile apps. Broker
integration for live portfolio sync. Social features.

Deferred does not mean rejected. It means v1 ships without it and we revisit
with evidence.

## 6. The data constraint, and how we live inside it

This deserves its own section because it is the one architectural decision
that cannot be undone later.

Market data reaches the platform on two entirely separate paths, and code on
one path must never write to the other:

**Public path.** Data we are permitted to store and serve to anyone. This is
end-of-day and fundamental data from sources whose terms allow redistribution,
or from the exchanges' own public disclosures. It populates the shared
database and everything an anonymous visitor sees.

**Private path (later phase).** If a signed-in user connects their own broker
account, that data is fetched with their credentials, shown only to them, and
never written into the shared store or served to another user. It is their
licensed data, not ours.

Phase 5 will verify the specific terms of every source before we depend on it,
source by source, in writing. Nothing gets ingested on the strength of a
recollection that it was probably fine. The earlier research that led to this
two-path split needs re-confirming against current terms as part of that
phase.

## 7. What success looks like

Not revenue, and not users. This is a portfolio and learning project first, so
the criteria are about whether the thing is real:

1. A stranger can open the URL, screen the NSE universe on fundamentals, and
   understand where every number came from.
2. Ingestion runs unattended on a schedule, and when it fails or a source goes
   missing, that is visible rather than silent.
3. Running cost is zero at current scale, and the path off the free tier is a
   documented change of configuration rather than a rewrite.
4. Someone technical can read the architecture doc and the code together and
   find that they agree.
5. It is explainable in an interview as a system, not as a set of features.

## 8. Decisions

**8.1 Market coverage — NSE-listed Indian equities only.** *Settled
2026-09-11.* Free data exists, the space is less saturated than US-market
tooling, and it is the market we actually understand. Consequence for Phase 4:
**do not hardcode a single exchange.** Instruments carry an exchange, and
currency is a field rather than an assumption. Adding another market later
should be a data-source problem, not a migration.

**8.2 Fundamentals are in v1.** *Settled 2026-09-11.* This is the
differentiator and the real data-engineering work. It is also the hardest
ingestion problem in the project: statements arrive irregularly, restatements
happen, and line items are not consistently named across companies. Phase 5
must treat fundamentals sourcing as its main risk, not an afterthought behind
price data.

**8.3 Public read without an account — yes.** Anonymous visitors get the full
read surface. Auth guards only personalised state: watchlists, portfolio,
alerts.

**8.4 Project name — pending.** A shorter name is being chosen.
`investment-intelligence` remains the working directory name until then.
Renaming is cheap now and expensive once the repository is public and linked
from a CV, so this blocks publication but not Phase 2.

---

## Exit criteria for Phase 1

- [x] Sections 3 and 5 signed off — the non-goals matter more than the goals
- [x] Open decisions 8.1, 8.2, 8.3 answered
- [ ] Project name chosen (blocks publication, not Phase 2)
- [x] Phase 2 may begin
