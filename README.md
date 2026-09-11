# Investment Intelligence Platform

*(working name — see open decision 8.4)*

A web platform for exploring and analysing Indian listed equities. Ingests
end-of-day market data and company fundamentals, stores them in a queryable
form, and exposes screening, peer comparison and charting on top of them.

Every number it shows can be traced back to the figure it came from and the
date it was ingested.

**It does not give investment advice.** No buy/sell calls, no target prices,
no picks. It presents data and computed metrics; conclusions are the reader's.
See [docs/01-vision-and-scope.md](docs/01-vision-and-scope.md) §3.

## Status

Phase 1 of 14. Nothing is built yet. See [ROADMAP.md](ROADMAP.md).

## Not to be confused with

[`paper-trader`](https://github.com/Vivek-Reddy7/paper-trader) is a separate,
standalone project: a market data pipeline and backtesting engine. The two
share ideas — idempotent ingestion, validation at the boundary, reconciling
derived state against a log — but they are deliberately separate codebases
with separate repositories and separate lifecycles. Neither imports the other.

## Documents

| Document | What it covers |
|---|---|
| [ROADMAP.md](ROADMAP.md) | The 14 phases, their objectives and exit criteria |
| [docs/01-vision-and-scope.md](docs/01-vision-and-scope.md) | What this is, what it is not, v1 scope |
