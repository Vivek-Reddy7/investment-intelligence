# Backlog

Things noticed mid-phase that are out of scope for the phase we are in.
Nothing here is a commitment. It exists so that a good idea does not derail
the phase it turned up in.

| Noted | Item | Earliest phase |
|---|---|---|
| 2026-09-11 | Corporate actions handling (splits, bonuses, rights) — affects price adjustment correctness | 4 |
| 2026-09-11 | Exchange holiday calendar, so weekday gaps stop reading as missing data | 6 |
| 2026-09-11 | Consume `paper-trader` as a library for strategy backtesting on screened baskets | 14 |
| 2026-09-11 | **Recover ~215 rejected quarterly facts.** Spans of 89-91 days match the quarterly band but are rejected because EDGAR's `fp` says `FY` rather than `Q1`-`Q4`. The quarter is derivable from `period_end` relative to the company's fiscal year end, which is itself derivable from its annual periods. Deterministic, and better than trusting `fp`. Found by Phase 17 rejection classification | 7 |
| 2026-09-11 | **Nine-month year-to-date periods have no home.** 151 rejections, all Genpact: spans of 272-273 days. US 10-Q filers report cumulative YTD. Needs a `9M` value in `period_type`'s CHECK constraint and a span band, plus a decision on whether YTD figures should be stored at all or derived from quarters | 7 |
| 2026-09-11 | Fiscal-year-end month differs per company (Genpact is December, Indian filers are March). Currently `_fiscal_year` assumes March. Correct for the Indian companies and wrong for Genpact — which is why its FY labels should not be trusted | 7 |
