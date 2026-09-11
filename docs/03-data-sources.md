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

## 3. What is probably not licensed — and needs verifying next

The finding in §1 is about **market data**: prices, volumes, quotes. It says
nothing about **company financial statements**, which are statutory public
disclosures filed by companies rather than exchange-generated market data.

If fundamentals are usable, then a fundamentals-driven screener is buildable
publicly while prices are not. That is the single most important open question
left in this phase, because it determines whether the product survives §1 in
recognisable form.

Specific items to verify, in priority order:

1. Company financial statements — can filed results be displayed publicly and
   in derived form? Source: SEBI LODR disclosure requirements, exchange
   filing-archive terms.
2. Corporate actions — splits, bonuses, dividends. Statutory disclosures, but
   confirm the archive's terms.
3. Historical index constituents — already flagged as the hardest item, and
   NSE index data is likely to sit under the same licensing regime as market
   data.
4. Whether any authorised vendor offers redistribution rights at a price
   compatible with a zero-revenue project.

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
- [ ] NSE Data Usage and Sharing Policy read in full (fetch timed out)
- [ ] Kite Connect terms of service located and redistribution clause quoted
- [ ] Company-filings licensing position established
- [ ] Corporate-actions source and terms established
- [ ] Historical index constituents source and terms established
- [ ] Upstox / Dhan / Groww / Fyers terms read
- [ ] **Go/no-go on a publicly accessible product, and on what data**
