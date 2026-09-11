# Phase 3 — Data Sources and Legal Verification

Status: **in progress — one blocking finding, decision required**
Last updated: 2026-09-11

Method: primary sources only. Where a document could not be read directly, it
is marked unverified rather than paraphrased from memory or from a secondary
article. Prices and terms in this space change; every claim below carries the
document it came from.

---

## 1. BLOCKING FINDING — displaying NSE market data publicly is licensed, and priced

This is the finding that changes the plan. It was verified from NSE's own
tariff documents.

### 1.1 End-of-day data

From [NSE's End of the Day Data Tariff](https://nsearchives.nseindia.com/s3fs-public/inline-files/Download%20EOD%20data%20tariff.pdf):

| Feed | Domestic (INR) — *Display on Website/Application/Information tools* |
|---|---|
| Capital Market and Futures & Options | **₹1,10,000** |
| Wholesale Debt Market | ₹10,000 |

Verbatim notes from that document:

> "the above-mentioned commercials are as per medium of data display i.e
> Website/App being two different mediums."

> "Real time market data vendors of NSE are allowed to provide End of Day
> (EOD) data on their proprietary terminal/software without any additional
> fees for display. However, if EOD / Previous Day EOD data is provided to
> vendor's clients for any type of usage, written consent from NSE Data &
> Analytics Ltd is mandatory."

So a website displaying NSE EOD capital-market data sits in a ₹1,10,000
bracket, and a mobile app would be a second charge. The tariff does not state
the billing period on its face — **presumed annual, unverified.**

### 1.2 Delayed data is not an escape hatch

From [NSE's 15-Min delayed data tariff](https://archives.nseindia.com/content/press/Snapshot_15_delayed_data.pdf):

| Segment | Domestic (INR) |
|---|---|
| Capital Market | **₹60,000** |
| Futures and Options | ₹60,000 |
| Currency Derivatives | ₹60,000 |
| Wholesale Debt Market | ₹60,000 |

> "The above-mentioned commercials are per medium of data display. For the
> sake of clarity, website and mobile app are considered two different mediums
> and charged separately."

> "Commercials are same even if the data is delayed by more than 15 minutes.
> For example, commercials for 20 min/30 min/1-hour etc. delayed feed is same
> as 15-minute delayed feed."

That last note kills the obvious workaround. Delaying data further does not
reduce the fee. There is no delay long enough to make display free.

### 1.3 Redistribution generally

NSE's Data Usage and Data Sharing Policy states that trading members and
subscribers may not redistribute market data except as agreed in a relevant
agreement, and that external redistribution of derived data requires a
separate agreement and fees. *Sourced from search extracts of the policy PDF;
the document itself timed out on direct fetch and should be read in full
before we rely on any nuance in it.*

### 1.4 What this does and does not prohibit

**Does not prohibit:** downloading the public bhavcopy and using it for our
own analysis, privately. Personal use is not display to third parties.

**Does prohibit, without a licence:** putting that data on a public website.

The distinction is *display to others*, not *acquisition*. Which means the
constraint lands precisely on the thing Phase 1 §5 committed to — a publicly
accessible platform.

---

## 2. Broker APIs — verified

### 2.1 Zerodha Kite Connect

From [Zerodha's API product page](https://zerodha.com/products/api/) and
[support articles](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/historical-data-and-live-market-data-payment-plan):

| Tier | Cost | Market data | Holdings / positions / orders / funds |
|---|---|---|---|
| Personal API | **Free** | **No** | Yes |
| Connect | ₹500/mo per API key | Yes, incl. up to 10y historical | Yes |

Historical data ceased to be a separate charge in February 2025; the Connect
tier has since come down from ₹2,000/mo to ₹500/mo.

**Useful consequence.** The free tier exposes exactly the data the private
per-user path needs — the user's own holdings, positions, orders and funds —
and none of the market data we are not permitted to redistribute anyway. So
Phase 21 can be built at zero cost, and the ₹500/mo tier buys data we could
not legally publish.

**Not verified:** the redistribution clause. The Kite Connect v3 documentation
contains no licensing or redistribution terms — that page is purely technical.
The restriction lives in the separate terms of service. Phase 3 must locate
and quote that document rather than inferring it from the exchange policy,
even though the exchange policy alone is sufficient to reach the same
conclusion.

### 2.2 Angel One SmartAPI

Free, with live and historical data and a WebSocket feed. Same redistribution
constraint applies, because it derives from exchange policy rather than broker
policy.

**Material change from 1 April 2026**, per
[Angel One's own notice](https://www.angelone.in/news/market-updates/what-s-changing-in-angel-one-s-smartapi-access-from-april-1-2026):

> "Effective from 01-Apr-2026, API order execution will only be accepted if it
> originates from your registered primary static IP."

Also: threshold of 10 orders per second per exchange segment; market and IOC
orders prohibited for algorithmic trading; retail algos must be hosted on the
broker's server unless the client maintains registered static IPs; registered
IPs changeable no more than once per calendar week.

**Assessment for us: mostly irrelevant, and that is worth knowing.** Every one
of those constraints attaches to *order execution*. This platform places no
orders (Phase 1 §3), so the static-IP requirement and the rate limits do not
bind us. It does mean that if the roadmap ever drifts toward execution, the
hosting model stops being free-tier-compatible.

### 2.3 Not yet verified

Upstox, Dhan, Groww, Fyers. Terms not read. The earlier summary of their free
tiers was from recollection and should not be treated as current. Lower
priority now, because §1 means the *public* data problem is not solved by any
broker regardless of its terms.

---

## 3. The NSE policy read in full — four findings

Obtained directly: [NSE Data Usage and Data Sharing Policy](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/NSE_Data_Sharing&Usage_Policy.pdf),
12 pages. This is the document the tariffs sit under, and it is more
consequential than the tariffs.

### 3.1 "Market Data" is defined very broadly — but it is scoped to NSE as the source

Clause 13(d), verbatim:

> "**Market Data** means any data and information (including any figures,
> statistics, numbers) in relation to any securities and/or derivatives
> contracts (including price, identifiers, volume, trade related data) **as
> well as any company**. This includes, without limitation, online streaming
> data, real time data (live feed data), snapshot data, delayed data, end of
> day data, historical data, tick by tick order and trade data **and corporate
> data** which may be transmitted to the Subscribers by NSE or NSE Data."

The first read of this is alarming — "in relation to … any company" and
"corporate data" would sweep in fundamentals.

The operative qualifier is the last clause: **"which may be transmitted to the
Subscribers by NSE or NSE Data."** The policy governs data *obtained from NSE*.
Clause 7.2 confirms the same framing:

> "The ownership of all Market Data shall at all times lie with NSE/ NSE Data."

— but again of "such Market Data licensed by NSE or NSE Data".

So what NSE owns and licenses is **its dataset**, not the underlying facts. A
company's reported revenue is not NSE's property. NSE's compiled, transmitted
feed of it is.

**This is the distinction the whole product now rests on**, so it should be
tested by a lawyer before public launch rather than by me. Nothing below is
legal advice.

### 3.2 The non-commercial route exists and is a dead end for us

Clause 8.3 offers hope:

> "The Board of NSE Data may also consider introducing reduced fee
> arrangements or waivers for Non-Commercial Users."

Clause 13(e) defines Non-Commercial Users to include "Researchers, Students
etc." — which arguably fits.

But clause 9.1 requires any such request to be "routed through Economic Policy
Research Department" with prior approval of NSE's managing director, and 9.3
closes it off entirely:

> "All Non-Commercial Users, Research Entities and Analysts (whether
> commissioned for research in terms of Clause 9.1 or otherwise) shall sign a
> **declaration of confidentiality** as part of their underlying
> documentation."

A confidentiality declaration is incompatible with publishing the data on a
public website. **The non-commercial waiver cannot produce a public platform.**
Route closed, decisively.

### 3.3 New hard prohibition — simulation and virtual trading are banned outright

Clause 7.4, verbatim:

> "The Market Data shall not be provided to
> individuals/entities/platforms/apps/websites etc., engaging in online
> gaming, **virtual trading or simulation**, fantasy games, an activity of
> similar nature or any other activity which shall be prohibited by the
> applicable laws or regulator…"

This is a **prohibition, not a fee.** No amount of money buys it.

Consequences worth recording:

- If we ever licensed NSE market data, we could not offer paper trading or
  portfolio simulation on top of it.
- Phase 22's "screen backtesting via `paper-trader` as a library" is at risk
  under this clause if it were to run on NSE-licensed data. Backtesting a
  screen is arguably simulation.
- `paper-trader` itself is unaffected today — it uses no NSE data — but this
  means it could never legally be migrated onto an NSE feed either.

### 3.4 Index construction needs a separate licence

Clause 7.1(a) prohibits using Market Data to create any financial index,
custom or composite, without separate licensing. Relevant to any future
"ranking" feature that resembles index construction.

---

## 4. Company filings — the route that works

### 4.1 Statements are statutorily public, and published by the company

SEBI LODR Regulation 33 requires listed entities to file quarterly and annual
financial results. Regulation 47 requires publication in newspapers. Current
practice requires the newspaper advertisement to carry a QR code and the
webpage address **where the complete financial results are available on the
company's own website**.

So the financial statements are: mandated to be public, published by the
company, and available from the company directly. Their public availability
does not depend on NSE transmitting them.

### 4.2 This is what existing platforms actually do

Screener.in computes its metrics from audited regulatory filings and links
through to the exchange filings repository for source documents. It is not
reselling an exchange price feed to produce those financials. That is the same
route described in §4.1, operating at scale, in public, for years.

Practical precedent is not a legal opinion, but a decade of a well-known
Indian platform doing exactly this is meaningful evidence.

### 4.3 Facts versus compilations

Indian copyright law does not protect facts, and has rejected the
"sweat of the brow" standard in favour of requiring a modicum of creativity.
Reported revenue for a quarter is a fact. A particular compilation or
presentation of many such facts may be protected; the numbers themselves are
not.

So the defensible position is: **obtain figures from the company's own
statutory disclosures, normalise them ourselves, present them in our own
form.** Not: copy someone else's database.

### 4.4 Still unverified

- **BSE's terms** for its filings repository — not read. BSE's *market data*
  regime is presumably similar to NSE's; the filings archive is a different
  thing and needs checking separately.
- **Corporate actions** — splits, bonuses, dividends. Statutory disclosures,
  but the archive's terms are unread.
- **Historical index constituents** — almost certainly sits under NSE's index
  licensing (§3.4). Assume unavailable until proven otherwise.

---

## 5. Conclusion — the product that is legally buildable, free, and public

| Data | Public display, free? | Source |
|---|---|---|
| Company fundamentals | **Yes** (§4) | Company statutory disclosures |
| Derived fundamental metrics | **Yes** | Computed by us from the above |
| Restatement history | **Yes** | Successive filings |
| Prices / OHLCV | **No** — ₹1,10,000 per medium | NSE licence required |
| Market cap, P/E, P/B | **No** — needs prices | — |
| Index membership, historical | **Assume no** (§3.4) | — |
| A user's own holdings and P&L | **Yes, to that user only** | Their broker, free tier |

### The resolution

**A publicly accessible, point-in-time fundamentals platform is buildable at
zero cost.** Screening on ROCE, margins, growth, leverage, cash conversion and
their history — all as-of-dateable, all traceable to a filing — needs no price
data and no NSE licence.

**Anything price-derived moves behind the private path**, where a signed-in
user connects their own broker account under Kite's free Personal tier and
sees prices and valuation computed against their own licensed data.

This is exactly the two-path split designed in Phase 4 §2.11, arrived at
independently from a different direction. The architecture needed no change to
absorb the largest finding in the project — which is the strongest possible
argument for having written it before any code.

### What the product loses, stated plainly

No public price charts. No public market cap, P/E or P/B. No public
large/mid/small-cap classification. A fundamentals screener without valuation
ratios is a genuinely narrower product than what Phase 1 described, and that
should be acknowledged rather than spun.

What it keeps is the wedge, intact: nobody free in India lets you ask what a
fundamentals screen would have returned on a past date using only what was
known then.

---

## 4. Consequences for earlier decisions

| Decision | Status after this phase |
|---|---|
| 8.1 NSE equities | Holds |
| 8.2 Fundamentals in v1 | **Now the load-bearing decision, not a differentiator.** It may be the only publicly displayable data we have |
| 8.3 Public read without account | **In conflict with §1** for any price-derived display |
| 8.5 Point-in-time wedge | Holds for fundamentals. Valuation ratios need prices, so partly affected |
| 8.6 NIFTY 500 universe | Index membership likely licensed — see §3 item 3 |
| Architecture two-path split | **Strongly vindicated.** It was designed for exactly this |

The architecture survives this finding without change, which is the point of
having done Phase 4 before Phase 6. What changes is scope, not structure.

---

## 5. Exit criteria — not yet met

- [x] Zerodha Kite Connect tiers and costs verified from primary sources
- [x] Angel One SmartAPI April 2026 changes verified from primary source
- [x] NSE EOD and delayed-data display tariffs verified from primary sources
- [x] NSE Data Usage and Sharing Policy read in full — 4 findings, §3
- [x] Company-filings position established — §4
- [x] **Go/no-go reached: GO, fundamentals public, prices private** — §5
- [ ] BSE filings repository terms read
- [ ] Corporate-actions source and terms established
- [ ] Historical index constituents — assume unavailable, confirm
- [ ] Kite Connect terms of service located and redistribution clause quoted
- [ ] Upstox / Dhan / Groww / Fyers terms read — now low priority (§5)
- [ ] **Legal review of the §3.1 facts-versus-feed distinction before public
      launch.** This is the load-bearing legal reading in the whole project and
      it should not rest on my analysis

## Disclaimer

This document is engineering research, not legal advice. The §3.1 distinction
between exchange-transmitted data and the underlying facts, and the §4.3
reading of Indian copyright law, both warrant a qualified opinion before the
platform is publicly accessible.
