# Phase 3 addendum — Commercial fundamentals APIs: pricing and terms

Status: **research complete, decision required**
Last updated: 2026-09-11
Follows: [03-data-sources.md](03-data-sources.md) §6

Question: can we buy our way past the Phase 7 blocker with a commercial
fundamentals API, at a price a zero-revenue project can justify?

Short answer: **no off-the-shelf plan grants the right to display the data
publicly.** The cheap providers do not grant it; the providers who could grant
it price it as a negotiated enterprise agreement. And separately, none of them
can deliver the product's actual differentiator.

---

## 1. The distinction every provider draws

EODHD's own documentation states the principle more clearly than any terms
page does:

> "Access to data is not the same as the right to display it, redistribute it,
> or package it inside a product, which is why licensing needs to be treated
> as a separate decision factor."

That is the whole finding. A subscription buys *access for your own use*.
Putting the data in front of other people is a separate right, separately
priced. Every provider surveyed draws this line, in the same place.

## 2. What was surveyed

### 2.1 BharatStock — cheapest, India-native, no display right

[bharatstockapi.com](https://bharatstockapi.com/)

| Plan | Price | Daily requests |
|---|---|---|
| Free | ₹0 | 50 |
| Starter | **₹500/mo** | 2,000 |
| Developer | ₹750/mo | 10,000 |
| Pro | ₹2,400/mo | 100,000 |

All tiers get the same data: 10 years of EOD prices, quarterly and annual P&L,
balance sheet and cash flow, corporate actions, shareholding patterns,
valuation ratios. Self-described sourcing:

> "Directly from NSE's own official sources — bhavcopy files for prices, XBRL
> filings for financials, and NSE's corporate filing feeds for actions and
> shareholding patterns. Nothing here is scraped through a third-party
> aggregator."

On paper this is exactly what Phase 7 needs, at ₹500/month.

**The problem is the terms.** They are generic platform boilerplate:

> "The contents of the Platform and the Services are proprietary to us and are
> licensed to us. You will not have any authority to claim any intellectual
> property rights, title, or interest in its contents."

No grant of redistribution or public display. Nothing about caching or storing
the data, which we would have to do (architecture §1: no serving path
triggers an external fetch). And **no claim to hold an exchange vendor
licence** — notable, because vendors who hold one advertise it prominently.

So building a public site on BharatStock would mean relying on a third party's
unverified redistribution rights. That is the laundering version of the thing
we already declined to do directly, and it falls under the standing
instruction: no scraping, no reverse-engineering, no violating terms of
service.

**Verdict:** excellent value for private use. No basis for public display.
Even private use with local storage needs confirming, since their terms do not
address caching.

### 2.2 TrueData — authorised, and that is exactly why it is expensive

[truedata.in](https://www.truedata.in/)

> "TrueData is an authorised real-time market data vendor for NSE, BSE and
> MCX."

Covers prices, fundamentals, corporate announcements and actions. Free
15-minute-delayed tier for use. And then the sentence that matters:

> "per-user exchange fees are levied as per the prevailing exchange tariff"

**Authorisation does not make display free. It makes display licensable.** The
₹60,000 and ₹1,10,000 tariffs from §1 of the main document reappear here,
passed through the vendor. That is the honest structure of this market: the
tariff follows the data to the end user, whoever the intermediary is.

**Verdict:** the legally clean route, and it reprices the project into
five-to-six figures annually.

### 2.3 Financial Modeling Prep — display is a separate agreement, explicitly

> "Displaying or redistributing data sourced from FMP requires a specific Data
> Display and Licensing Agreement with FMP."

The clearest statement of the pattern. Standard plans do not include display.

### 2.4 EODHD — retail tiers are not for commercial use

[eodhd.com/pricing](https://eodhd.com/pricing)

| Plan | Price/mo | Notes |
|---|---|---|
| Free | $0 | 20 API calls/day, no fundamentals |
| EOD Historical | $19.99 | No fundamentals |
| **Fundamentals Data Feed** | **$59.99** | Non-US fundamentals from 2000 |
| ALL-IN-ONE | $99.99 | Everything |

> "For commercial use, choose Startups & Enterprise Data Solution Plan, and we
> will reach out to you shortly."

So the published prices are personal-use prices. Commercial use is quote-only.
India coverage is also unconfirmed — the site advertises 150,000+ tickers
worldwide without naming NSE.

### 2.5 Intrinio — enterprise pricing, NSE prices available

Carries NSE price data. Pricing is packaged and quote-based. Not evaluated
further: it is in the same category as TrueData, which is to say a real
licence at a real price.

---

## 3. The finding that matters more than price

Set aside licensing entirely for a moment. **None of these providers can
deliver the product's differentiator.**

The wedge (decision 8.5) is point-in-time correctness: what a screen would
have returned on a past date, using only what was known then. That requires
**as-reported figures** — the original number, before restatement.

Commercial APIs serve the *current, restated* view. When a company restates,
the vendor's database is updated and the original figure is gone. That is the
same overwrite we identified in Screener.in and Tickertape, and it is why they
cannot answer the question either.

So paying ₹500/month, or $59.99/month, or a negotiated enterprise fee, buys a
faster path to a **conventional** screener. It does not buy the wedge. The
wedge is only obtainable two ways:

1. **From primary filings**, which contain the figures as originally reported
   and remain available as separate documents after a restatement.
2. **Forward-only from our own first ingestion**, accumulating as-reported
   history from today onward — which is what a vendor feed would give us, with
   a cold start measured in quarters.

That reframes the choice. The filings route is not merely the cheap option. It
is the only route that delivers the product we decided to build, with history.

---

## 4. The four real options

| # | Route | Cost | Public? | Wedge with history? | Effort |
|---|---|---|---|---|---|
| 1 | **Self-source from company filings** | ₹0 | Yes | **Yes** | High — ~500 sites and formats |
| 2 | **BharatStock, private tool behind login** | ₹500/mo | No | No — forward only | Low |
| 3 | **Authorised vendor / NSE licence** | ₹1.1L+/yr | Yes | No — restated feed | Low |
| 4 | **Hybrid** — public fundamentals self-sourced; prices per-user via broker | ₹0 | Yes | **Yes** | High |

Option 4 is option 1 plus the private broker path already designed in
architecture §2.11, and it is the only one that satisfies every constraint we
have accepted: free, public, point-in-time correct, and licensed cleanly.

It is also, by some distance, the most work.

## 5. Recommendation

**Option 4, reached in two stages.**

Stage one: build the filings adapter against a *small* set of companies — ten,
not five hundred — chosen for format consistency. That is enough to prove the
parser, exercise the backfill engine against real documents, and reach a
public URL with genuine data. The Phase 7 engine already supports this: the
tracked set is configuration.

Stage two: widen coverage as the parser learns formats, with
`backfill_checkpoints` reporting honestly on what is and is not loaded. A
platform covering 50 companies well, and saying so, is more credible than one
claiming 500 and silently missing 14%.

**Consider option 2 in parallel, for a different reason.** ₹500/month for a
private instance with full prices and valuation is cheap, and it de-risks the
parser work by giving a reference dataset to check our own extraction against.
A discrepancy between our figure and theirs is a signal worth having. That is
internal validation use, not display, so the terms question does not arise —
though their silence on caching should still be confirmed with them directly.

## 6. What to verify before spending anything

- [ ] BharatStock: written confirmation on storing/caching, and on whether any
      display right exists at any tier
- [ ] BharatStock: whether they hold an exchange vendor licence
- [ ] EODHD: India/NSE fundamentals coverage, and a quote for commercial use
- [ ] Whether any provider offers as-reported (unrestated) historical
      fundamentals at all — asked explicitly, because none advertise it

## Disclaimer

Engineering research, not legal advice. The conclusion that a vendor cannot
pass on display rights it has not itself licensed is a reading of the terms
above and of NSE's policy, and warrants a qualified opinion before the
platform is publicly accessible.
