"""Command line: seed the tracked set, run a backfill, report gaps.

    python -m investment_intelligence.cli seed
    python -m investment_intelligence.cli backfill --since 2015-01-01
    python -m investment_intelligence.cli gaps
    python -m investment_intelligence.cli show --ref 1067491

Separate commands on purpose, the same reasoning as in `paper-trader`:
fetching is slow, rate limited and fails; reading should be instant and
repeatable. Splitting them means a strategy or a metric can be changed fifty
times without touching the network.
"""

from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal

from investment_intelligence.companies import STAGE_ONE
from investment_intelligence.db import connect, migrate
from investment_intelligence.ingest import backfill, incremental
from investment_intelligence.ingest import rejections as rejection_log
from investment_intelligence.observability import logging as structured
from investment_intelligence.observability import status as status_mod
from investment_intelligence.ingest import price_writer
from investment_intelligence.analytics import (
    backtest, combined_score, factor_score, market_cap, risk, sizing, technicals,
)
from investment_intelligence.sources import classification
from investment_intelligence.sources.edgar import EdgarSource
from investment_intelligence.sources.prices import YFinancePriceSource

SOURCE_ID = "SEC_EDGAR"
JOB = "edgar-stage-one"

PRICE_SOURCE_ID = "YAHOO_FINANCE"

# redistributable=False is load-bearing, not decorative: see migration 024
# and docs/03-data-sources.md §7. This source's data is fetched and stored
# for local research/pipeline validation only, and must not be served by the
# public web app until this row's terms change or the source is replaced.
PRICE_SOURCE_ROW = dict(
    source_id=PRICE_SOURCE_ID,
    name="Yahoo Finance (via yfinance, unofficial)",
    kind="MARKET_DATA_VENDOR",
    licence_note=(
        "Yahoo's Developer API Terms of Use prohibit automated access "
        "without written permission and prohibit redistributing or "
        "monetising API data without a licence. Yahoo retired its official "
        "API in 2017; yfinance calls internal endpoints with no licence at "
        "all. Personal/local research use is low-risk; a public, "
        "customer-facing product republishing this data is the higher-risk "
        "case Yahoo's own terms name explicitly. NOT cleared for public "
        "serving -- see docs/03-data-sources.md §7."
    ),
    licence_url="https://legal.yahoo.com/us/en/yahoo/terms/product-atos/apiforydn/index.html",
    redistributable=False,
    verified_on=date(2026, 9, 22),
)

# The licence position, recorded in the database rather than in a comment.
# `sources` refuses a row without a non-empty note and a verification date
# (001_sources.sql), so a source nobody has checked cannot be ingested from.
SOURCE_ROW = dict(
    source_id=SOURCE_ID,
    name="SEC EDGAR XBRL companyfacts",
    kind="FILINGS",
    licence_note=(
        "SEC webmaster FAQ: 'All Government-created content on sec.gov and "
        "EDGAR public filing content are free to access and reuse.' "
        "Access policy: max 10 requests/second, declared User-Agent required."
    ),
    licence_url="https://www.sec.gov/os/webmaster-faq",
    redistributable=True,
    verified_on=date(2026, 9, 11),
)


def cmd_seed(args: argparse.Namespace) -> None:
    with connect() as conn:
        migrate(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sources (source_id, name, kind, licence_note,
                                     licence_url, redistributable, verified_on)
                VALUES (%(source_id)s, %(name)s, %(kind)s, %(licence_note)s,
                        %(licence_url)s, %(redistributable)s, %(verified_on)s)
                ON CONFLICT (source_id) DO UPDATE
                   SET licence_note = excluded.licence_note,
                       licence_url  = excluded.licence_url,
                       verified_on  = excluded.verified_on
                """,
                SOURCE_ROW,
            )

            for company in STAGE_ONE:
                cur.execute(
                    "SELECT instrument_id FROM instrument_external_ids "
                    "WHERE scheme = 'SEC_CIK' AND value = %s",
                    (str(company.cik),),
                )
                row = cur.fetchone()
                if row:
                    instrument_id = row[0]
                else:
                    cur.execute(
                        "INSERT INTO instruments (asset_class, currency) "
                        "VALUES ('EQUITY', 'INR') RETURNING instrument_id"
                    )
                    instrument_id = cur.fetchone()[0]
                    cur.execute(
                        "INSERT INTO instrument_external_ids "
                        "(instrument_id, scheme, value, source_id) "
                        "VALUES (%s, 'SEC_CIK', %s, %s), (%s, 'US_TICKER', %s, %s)",
                        (instrument_id, str(company.cik), SOURCE_ID,
                         instrument_id, company.us_ticker, SOURCE_ID),
                    )
                cur.execute(
                    "INSERT INTO tracked_instruments (instrument_id, reason) "
                    "VALUES (%s, 'EDGAR_STAGE_ONE') ON CONFLICT DO NOTHING",
                    (instrument_id,),
                )
        conn.commit()
    print(f"seeded {len(STAGE_ONE)} companies and source {SOURCE_ID}")


def _source() -> EdgarSource:
    return EdgarSource(cik_by_ref={str(c.cik): c.cik for c in STAGE_ONE})


def cmd_backfill(args: argparse.Namespace) -> None:
    # SEC allows 10 req/s. We take one every 0.5s: two orders of magnitude
    # inside the limit, because being banned costs more than being slow and a
    # backfill has no deadline.
    limiter = backfill.RateLimiter(0.5)
    with connect() as conn:
        report = backfill.run(
            conn, _source(), job=JOB,
            since=date.fromisoformat(args.since),
            until=date.fromisoformat(args.until),
            limiter=limiter,
            max_instruments=args.limit,
        )
    print(f"run {report.run_id}: {report.outcome}")
    print(f"  done={report.instruments_done} skipped={report.instruments_skipped} "
          f"failed={report.instruments_failed}")
    print(f"  facts written={report.writes.facts_written} "
          f"unchanged={report.writes.facts_unchanged} "
          f"filings={report.writes.filings_written}")
    print(f"  metrics refreshed={report.metrics_refreshed}")
    if report.rejections:
        print(f"  rejections={len(report.rejections)}; first few:")
        for rejection in report.rejections[:5]:
            print(f"    {rejection.instrument_ref}: {rejection.reason}")


def cmd_prices(args: argparse.Namespace) -> None:
    """Backfill daily price bars for the tracked set. Local research use
    only -- see PRICE_SOURCE_ROW and docs/03-data-sources.md §7."""
    source = YFinancePriceSource()
    since = date.fromisoformat(args.since)
    until = date.fromisoformat(args.until)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sources (source_id, name, kind, licence_note,
                                     licence_url, redistributable, verified_on)
                VALUES (%(source_id)s, %(name)s, %(kind)s, %(licence_note)s,
                        %(licence_url)s, %(redistributable)s, %(verified_on)s)
                ON CONFLICT (source_id) DO UPDATE
                   SET licence_note = excluded.licence_note,
                       licence_url  = excluded.licence_url,
                       verified_on  = excluded.verified_on
                """,
                PRICE_SOURCE_ROW,
            )
        conn.commit()

        total_written = total_unchanged = 0
        rejections: list = []
        for company in STAGE_ONE:
            bars = list(source.fetch_bars(company.us_ticker, since, until))
            result = price_writer.write_bars(conn, source, company.us_ticker, bars)
            total_written += result.bars_written
            total_unchanged += result.bars_unchanged
            rejections.extend(result.rejections)
            print(f"  {company.us_ticker:6s} written={result.bars_written:5d} "
                  f"unchanged={result.bars_unchanged:5d} "
                  f"rejected={len(result.rejections)}")
        conn.commit()

    print(f"\ntotal: written={total_written} unchanged={total_unchanged} "
          f"rejected={len(rejections)}")
    if rejections:
        reasons: dict[str, int] = {}
        for r in rejections:
            reasons[r.reason] = reasons.get(r.reason, 0) + 1
        for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"  {reason}: {count}")


def cmd_factors(args: argparse.Namespace) -> None:
    """Compute and store the factor score for `--as-of` (default: today).
    Reads price_bars and metrics_as_of; writes factor_scores. A calculation
    over the tracked universe, not a recommendation -- see
    analytics/factor_score.py's module docstring for why it is a rank
    combination and not a trained model at this sample size."""
    as_of = date.today() if args.as_of == "today" else date.fromisoformat(args.as_of)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT i.instrument_id, x.value FROM instruments i "
                "JOIN instrument_external_ids x ON x.instrument_id = i.instrument_id "
                "AND x.scheme = 'US_TICKER'"
            )
            id_to_ticker = dict(cur.fetchall())

        scores = factor_score.compute_scores(conn, list(id_to_ticker), as_of)
        written = factor_score.store_scores(conn, as_of, scores)
        conn.commit()

    print(f"as of {as_of}: {written} scores computed and stored "
          f"(model {factor_score.MODEL_VERSION})\n")

    # A score built from 1 of 3 factors is not comparable to one built from
    # all 3 -- IBN scores highest here on momentum alone, because its EDGAR
    # fundamentals 404 (the same expected gap from Phase 7), and momentum
    # happens to be its only available factor. Ranking it against full-
    # coverage instruments would present a single-factor artefact as though
    # it beat a rounded assessment, so full and partial coverage are shown
    # separately rather than interleaved into one misleadingly total order.
    full = [s for s in scores if s.factors["factors_available"] == 3]
    partial = [s for s in scores if s.factors["factors_available"] < 3]

    print("ranked (all 3 factors available):")
    for s in sorted(full, key=lambda s: -s.composite_score):
        print(f"  {id_to_ticker[s.instrument_id]:6s} composite={float(s.composite_score):.3f}")

    if partial:
        print("\nnot ranked -- incomplete factor coverage, not comparable to the above:")
        for s in sorted(partial, key=lambda s: -s.composite_score):
            available = s.factors["factors_available"]
            have = list(s.factors["components"])
            print(f"  {id_to_ticker[s.instrument_id]:6s} composite={float(s.composite_score):.3f}  "
                  f"({available}/3: {', '.join(have)})")


def cmd_technicals(args: argparse.Namespace) -> None:
    """Compute and store technical indicators for `--as-of` (default: today).
    Calculations, not signals -- see analytics/technicals.py's module
    docstring. Local research only, same as `prices` and `factors`."""
    as_of = date.today() if args.as_of == "today" else date.fromisoformat(args.as_of)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT i.instrument_id, x.value FROM instruments i "
                "JOIN instrument_external_ids x ON x.instrument_id = i.instrument_id "
                "AND x.scheme = 'US_TICKER'"
            )
            id_to_ticker = dict(cur.fetchall())

        written = technicals.compute_and_store(conn, list(id_to_ticker), as_of)
        conn.commit()

        rows = []
        for iid in id_to_ticker:
            ind = technicals.compute(conn, iid, as_of)
            if ind:
                rows.append((id_to_ticker[iid], ind))

    print(f"as of {as_of}: {written} instruments computed and stored "
          f"(model {technicals.MODEL_VERSION})\n")
    for ticker, ind in sorted(rows):
        rsi = f"{float(ind['rsi_14']):.1f}" if "rsi_14" in ind else "-"
        cross = ind.get("golden_cross")
        cross_s = "golden" if cross is True else "death" if cross is False else "-"
        bo = ind.get("range_20d", {}).get("breakout", "-")
        print(f"  {ticker:6s} RSI={rsi:>6s}  cross={cross_s:6s}  breakout={bo}")


def cmd_risk(args: argparse.Namespace) -> None:
    """Compute and store risk metrics for `--as-of` (default: today):
    annualised volatility, max drawdown, historical 95% VaR. Describes the
    past, not a prediction -- see analytics/risk.py's module docstring.
    Local research only, same as `prices` and `technicals`."""
    as_of = date.today() if args.as_of == "today" else date.fromisoformat(args.as_of)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT i.instrument_id, x.value FROM instruments i "
                "JOIN instrument_external_ids x ON x.instrument_id = i.instrument_id "
                "AND x.scheme = 'US_TICKER'"
            )
            id_to_ticker = dict(cur.fetchall())

        written = risk.compute_and_store(conn, list(id_to_ticker), as_of)
        conn.commit()

        rows = []
        for iid in id_to_ticker:
            m = risk.compute(conn, iid, as_of)
            if m:
                rows.append((id_to_ticker[iid], m))

    print(f"as of {as_of}: {written} instruments computed and stored "
          f"(model {risk.MODEL_VERSION})\n")
    for ticker, m in sorted(rows, key=lambda r: -float(r[1].get("annualized_volatility", 0))):
        vol = f"{float(m['annualized_volatility']):.1%}" if "annualized_volatility" in m else "-"
        dd = f"{float(m['max_drawdown']):.1%}" if "max_drawdown" in m else "-"
        var = f"{float(m['historical_var_95']):.1%}" if "historical_var_95" in m else "-"
        print(f"  {ticker:6s} ann.vol={vol:>7s}  max drawdown={dd:>8s}  "
              f"1-day VaR(95%)={var:>7s}")


def cmd_combined(args: argparse.Namespace) -> None:
    """Compute and store the combined fundamental + technical score for
    `--as-of` (default: today). See analytics/combined_score.py -- five
    named factors, equal-weighted rank, not a trained model, not advice."""
    as_of = date.today() if args.as_of == "today" else date.fromisoformat(args.as_of)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT i.instrument_id, x.value FROM instruments i "
                "JOIN instrument_external_ids x ON x.instrument_id = i.instrument_id "
                "AND x.scheme = 'US_TICKER'"
            )
            id_to_ticker = dict(cur.fetchall())

        scores = combined_score.compute_scores(conn, list(id_to_ticker), as_of)
        written = factor_score.store_scores(conn, as_of, scores,
                                             model_version=combined_score.MODEL_VERSION)
        conn.commit()

    print(f"as of {as_of}: {written} scores computed and stored "
          f"(model {combined_score.MODEL_VERSION})\n")

    full = [s for s in scores if s.factors["factors_available"] == s.factors["factors_total"]]
    partial = [s for s in scores if s.factors["factors_available"] < s.factors["factors_total"]]

    print(f"ranked (all {5} factors available):")
    for s in sorted(full, key=lambda s: -s.composite_score):
        print(f"  {id_to_ticker[s.instrument_id]:6s} composite={float(s.composite_score):.3f}")

    if partial:
        print("\nnot ranked -- incomplete factor coverage, not comparable to the above:")
        for s in sorted(partial, key=lambda s: -s.composite_score):
            available, total = s.factors["factors_available"], s.factors["factors_total"]
            have = list(s.factors["components"])
            print(f"  {id_to_ticker[s.instrument_id]:6s} composite={float(s.composite_score):.3f}  "
                  f"({available}/{total}: {', '.join(have)})")


def cmd_size(args: argparse.Namespace) -> None:
    """Split --amount across the top --top ranked (combined score)
    instruments, equal-weighted. Arithmetic downstream of a screen already
    run -- see analytics/sizing.py's module docstring for why this makes no
    selection decision of its own."""
    amount = Decimal(args.amount)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT f.instrument_id, i.value, p.close
                FROM factor_scores f
                JOIN instrument_external_ids i
                  ON i.instrument_id = f.instrument_id AND i.scheme = 'US_TICKER'
                JOIN LATERAL (
                    SELECT close FROM price_bars pb
                    WHERE pb.instrument_id = f.instrument_id
                    ORDER BY day DESC LIMIT 1
                ) p ON true
                WHERE f.model_version = %s
                  AND f.as_of = (SELECT max(as_of) FROM factor_scores WHERE model_version = %s)
                  AND (f.factors->>'factors_available')::int = (f.factors->>'factors_total')::int
                ORDER BY f.composite_score DESC
                LIMIT %s
                """,
                (combined_score.MODEL_VERSION, combined_score.MODEL_VERSION, args.top),
            )
            rows = cur.fetchall()

    if not rows:
        print("no ranked, fully-covered instruments found -- run `combined` first")
        return

    holdings = [sizing.Holding(instrument_id=iid, ticker=ticker, price=price, currency=args.currency)
                for iid, ticker, price in rows]
    try:
        result = sizing.size_equal_weight(amount, args.currency, holdings)
    except sizing.MixedCurrency as exc:
        print(f"cannot size: {exc}")
        print("(the tracked universe is priced in USD; pass --currency USD, "
              "or size a group you have already confirmed share one currency)")
        return

    print(f"₹/${amount} {args.currency}, split equal-weight across the top {len(holdings)} "
          f"ranked instruments ({result.per_instrument_budget:.2f} {args.currency} each):\n")
    for a in result.allocations:
        print(f"  {a.ticker:6s} {a.shares:>4d} shares @ {a.price:>10.2f} = "
              f"{a.cost:>10.2f} {args.currency}")
    print(f"\n  spent:    {result.total_spent:.2f} {args.currency}")
    print(f"  leftover: {result.leftover:.2f} {args.currency}  "
          f"(uninvested cash -- whole shares only, no fractional-share brokerage assumed)")


def cmd_sectors(args: argparse.Namespace) -> None:
    """Fetch and store each tracked company's SIC code and sector, from SEC
    EDGAR filer metadata. NOT local-research-only -- see migration 027 for
    why this table's licensing posture differs from prices/factors/technicals."""
    results = classification.fetch_all([c.cik for c in STAGE_ONE])
    with connect() as conn:
        with conn.cursor() as cur:
            for r in results:
                cur.execute(
                    "SELECT instrument_id FROM instrument_external_ids "
                    "WHERE scheme = 'SEC_CIK' AND value = %s", (str(r.cik),))
                row = cur.fetchone()
                if row is None:
                    continue
                cur.execute(
                    """
                    INSERT INTO sector_classifications
                        (instrument_id, sic_code, sic_description, sector,
                         source_id, verified_on)
                    VALUES (%s, %s, %s, %s, 'SEC_EDGAR', %s)
                    ON CONFLICT (instrument_id) DO UPDATE
                       SET sic_code = excluded.sic_code,
                           sic_description = excluded.sic_description,
                           sector = excluded.sector,
                           verified_on = excluded.verified_on,
                           computed_at = now()
                    """,
                    (row[0], r.sic_code, r.sic_description, r.sector, date.today()),
                )
        conn.commit()

    print(f"{len(results)} of {len(STAGE_ONE)} tracked companies classified\n")
    by_sector: dict[str, list[str]] = {}
    ticker_by_cik = {c.cik: c.us_ticker for c in STAGE_ONE}
    for r in results:
        by_sector.setdefault(r.sector, []).append(ticker_by_cik.get(r.cik, str(r.cik)))
    for sector, tickers in sorted(by_sector.items()):
        print(f"  {sector:35s} {', '.join(sorted(tickers))}")


def cmd_marketcap(args: argparse.Namespace) -> None:
    """Compute and store market cap (shares outstanding * price) for
    `--as-of`. LOCAL RESEARCH ONLY -- price is yfinance-sourced. No cap-tier
    label; see migration 028 for why one is not produced."""
    as_of = date.today() if args.as_of == "today" else date.fromisoformat(args.as_of)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT i.instrument_id, x.value FROM instruments i "
                "JOIN instrument_external_ids x ON x.instrument_id = i.instrument_id "
                "AND x.scheme = 'US_TICKER'"
            )
            id_to_ticker = dict(cur.fetchall())

        results = []
        for company in STAGE_ONE:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT instrument_id FROM instrument_external_ids "
                    "WHERE scheme = 'SEC_CIK' AND value = %s", (str(company.cik),))
                row = cur.fetchone()
            if row is None:
                continue
            cap = market_cap.compute_and_store(conn, row[0], company.cik, as_of)
            if cap is not None:
                results.append((company.us_ticker, cap))
        conn.commit()

    print(f"as of {as_of}: {len(results)} of {len(STAGE_ONE)} tracked companies\n")
    for ticker, cap in sorted(results, key=lambda x: -x[1]):
        print(f"  {ticker:6s} ${cap:>20,.0f}")


def cmd_backtest(args: argparse.Namespace) -> None:
    """Recompute the combined score at each historical checkpoint between
    --since and --until, and check what each ranked instrument's price
    actually did over the following --forward-days. Reports the pooled
    information coefficient -- see analytics/backtest.py's module docstring
    for the full method and its honest caveats. LOCAL RESEARCH ONLY."""
    since = date.fromisoformat(args.since)
    until = date.fromisoformat(args.until)
    forward_days = args.forward_days
    every_days = args.every_days

    checkpoints = []
    d = since
    while d <= until:
        checkpoints.append(d)
        d = date.fromordinal(d.toordinal() + every_days)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT i.instrument_id, x.value FROM instruments i "
                "JOIN instrument_external_ids x ON x.instrument_id = i.instrument_id "
                "AND x.scheme = 'US_TICKER'"
            )
            id_to_ticker = dict(cur.fetchall())

        points = backtest.run(conn, list(id_to_ticker), checkpoints, forward_days)
        written = backtest.store(conn, points)
        conn.commit()

    ic = backtest.information_coefficient(points)
    print(f"{len(checkpoints)} checkpoints ({since} to {until}, every {every_days}d), "
          f"{forward_days}d forward window\n")
    print(f"{written} (as_of, instrument) points computed and stored\n")
    if ic["ic"] is None:
        print(f"information coefficient: undefined -- {ic['note']}")
    else:
        print(f"information coefficient: {ic['ic']:+.3f}")
        print(f"  {ic['note']}")
        direction = ("higher-ranked names tended to do BETTER afterward" if ic["ic"] > 0.1 else
                     "higher-ranked names tended to do WORSE afterward" if ic["ic"] < -0.1 else
                     "no clear relationship between rank and what happened next")
        print(f"  reading: {direction}")



def cmd_incremental(args: argparse.Namespace) -> None:
    """The daily job. Cheap, and its real output is the run-log row."""
    limiter = backfill.RateLimiter(0.5)
    with connect() as conn:
        report = incremental.run(conn, _source(), limiter=limiter,
                                 lookback_years=args.lookback)
    print(f"run {report.run_id}: {report.outcome}")
    print(f"  checked={report.instruments_checked} failed={report.instruments_failed}")
    print(f"  new filings={report.new_filings} "
          f"facts written={report.writes.facts_written} "
          f"unchanged={report.writes.facts_unchanged}")
    print(f"  metrics refreshed={report.metrics_refreshed}")
    if report.rejections:
        print(f"  rejections={len(report.rejections)}")
    # Non-zero exit on a failed cycle so the scheduler surfaces it. A job that
    # always exits 0 turns a red run into a green one.
    raise SystemExit(1 if report.outcome == "FAILED" else 0)


def cmd_health(args: argparse.Namespace) -> None:
    """Report expected-vs-actual ingestion. Exits non-zero if anything is off.

    This is the mitigation for GitHub Actions silently disabling a schedule
    after 60 days of repository inactivity (ADR 004). It answers a question a
    run-log query cannot: whether a run that should have happened did not.
    """
    with connect() as conn:
        rows = incremental.health(conn)
    if not rows:
        print("no jobs are scheduled — nothing is being monitored")
        raise SystemExit(1)
    bad = 0
    for row in rows:
        flag = "ok " if row["status"] == "OK" else "!! "
        if row["status"] != "OK":
            bad += 1
        age = row["age"]
        print(f"  {flag}{row['source_id']}/{row['kind']}: {row['status']}"
              f"  last success {row['last_success']}"
              f"  age {age if age else 'never'}  (limit {row['max_age']})")
    raise SystemExit(1 if bad else 0)


def cmd_schedule(args: argparse.Namespace) -> None:
    """Declare what we expect to run, so its absence is detectable."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ingestion_schedule (source_id, kind, max_age, note)
                VALUES (%s, 'FUNDAMENTALS', %s, %s)
                ON CONFLICT (source_id, kind) DO UPDATE
                   SET max_age = excluded.max_age, note = excluded.note
                """,
                (SOURCE_ID, f"{args.max_age_days} days",
                 "Annual filings: nothing new most days, but the JOB must still run."),
            )
        conn.commit()
    print(f"scheduled {SOURCE_ID}/FUNDAMENTALS, stale after {args.max_age_days} days")


def cmd_status(args: argparse.Namespace) -> None:
    """The operational snapshot. Exits non-zero if anything is wrong.

    Distinct from `health`, which answers one question. This is what an
    operator looks at when something is reported broken and they do not yet
    know what.
    """
    with connect() as conn:
        status_mod.detect(conn)
        conn.commit()
        snap = status_mod.snapshot(conn)

    print(f"OVERALL: {snap['overall']}")

    if snap.get("metrics_stale"):
        print(f"\n!! {snap['facts']} facts loaded but metric_values is EMPTY.")
        print("   Every live screen will return nothing while historical")
        print("   screens still work. Fix with:  make incremental")

    if snap["open_alerts"]:
        print("\nOPEN ALERTS")
        for a in snap["open_alerts"]:
            told = "notified" if a["notified_at"] else "NOT NOTIFIED"
            print(f"  [{a['severity']}] {a['source_id']}/{a['kind']}: {a['status']}  ({told})")
            print(f"      {a['detail']}")

    print("\nINGESTION HEALTH")
    if not snap["health"]:
        print("  nothing is scheduled — nothing is being monitored")
    for h in snap["health"]:
        mark = "ok" if h["status"] == "OK" else "!!"
        print(f"  {mark} {h['source_id']}/{h['kind']}: {h['status']}"
              f"  last success {h['last_success']}")

    print("\nRECENT RUNS")
    for r in snap["recent_runs"][:5]:
        print(f"  run {r['run_id']:>4} {r['outcome']:8s} {r['seconds'] or '?':>6}s"
              f"  written={r['rows_written']:<6} rejected={r['rows_rejected']}")

    if snap["inert_checks"]:
        # A check that never ran is not a passing check.
        print(f"\nINERT QUALITY CHECKS: {', '.join(snap['inert_checks'])}")

    cov = snap["coverage"]
    if cov:
        print(f"\nCOVERAGE  {cov['instruments']} instruments, {cov['facts']} facts, "
              f"{cov['period_types']} period types, {cov['earliest']} to {cov['latest']}")

    if snap["rejections"]:
        print("\nREJECTED AT INGESTION")
        for row in snap["rejections"]:
            print(f"  {row['reason_class']:26s} {row['rejections']:>5}")

    pending = snap["open_alerts"]
    raise SystemExit(1 if snap["overall"] in ("CRITICAL", "UNMONITORED") else 0)


def cmd_quality(args: argparse.Namespace) -> None:
    """The data quality report.

    Prints coverage alongside findings, always. Zero findings over zero
    evaluable periods is an inert check, not clean data, and reporting them
    the same way is actively reassuring.
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT refresh_quality_findings()")
            cur.execute("SELECT * FROM quality_coverage()")
            coverage = cur.fetchall()
        conn.commit()

        print("CHECKS")
        inert = 0
        errors = 0
        for code, severity, evaluable, findings in coverage:
            if evaluable == 0:
                mark, note = "??", "INERT — no periods could be evaluated"
                inert += 1
            elif findings == 0:
                mark, note = "ok", f"clean over {evaluable} periods"
            else:
                mark = "!!" if severity == "ERROR" else " ~"
                note = f"{findings} finding(s) over {evaluable} periods"
                if severity == "ERROR":
                    errors += findings
            print(f"  {mark} {code:24s} {severity:5s} {note}")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT q.severity, f.check_code, f.fiscal_year, f.detail "
                "FROM quality_findings f JOIN quality_checks q USING (check_code) "
                "ORDER BY q.severity, f.check_code LIMIT %s", (args.limit,))
            rows = cur.fetchall()
        if rows:
            print("\nFINDINGS")
            for severity, code, fy, detail in rows:
                print(f"  [{severity}] {code} FY{fy}: {detail}")

        print("\nREJECTED AT INGESTION")
        summary = rejection_log.summary(conn)
        if not summary:
            print("  none recorded")
        for row in summary:
            print(f"  {row['reason_class']:26s} {row['rejections']:>5}  "
                  f"across {row['instruments']:>2} instrument(s)")
        unclassified = rejection_log.unclassified(conn)
        if unclassified:
            # A growing UNCLASSIFIED bucket means the source started rejecting
            # things for a new reason and nobody noticed.
            print(f"  UNCLASSIFIED reasons needing a rule: {unclassified[:3]}")

    # Non-zero on an ERROR finding or an inert check. A clean report over
    # checks that never ran is the outcome this exit code exists to prevent.
    raise SystemExit(1 if (errors or inert) else 0)


def cmd_gaps(args: argparse.Namespace) -> None:
    with connect() as conn:
        total = backfill.planned(conn, JOB)
        found = backfill.gaps(conn, JOB, "SEC_CIK")
    if total == 0:
        # Distinct from "no gaps". An unplanned job has not succeeded, it has
        # not started, and saying "no gaps" here is the reassuring answer to
        # the wrong question.
        print(f"job {JOB!r} has no checkpoints — nothing has been planned or run")
        raise SystemExit(1)
    if not found:
        print(f"no gaps: all {total} tracked instruments are DONE")
        return
    for state, refs in found.items():
        print(f"{state} ({len(refs)}):")
        for ref in refs:
            print(f"  {ref}")


def cmd_show(args: argparse.Namespace) -> None:
    """Point-in-time readout for one company, to eyeball the wedge."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT f.fiscal_year, f.line_item, f.currency, f.value,
                   f.known_from::date, fl.source_ref
            FROM   facts_as_of(%s) f
            JOIN   instrument_external_ids x USING (instrument_id)
            JOIN   filings fl ON fl.filing_id = f.filing_id
            WHERE  x.scheme = 'SEC_CIK' AND x.value = %s
              AND  f.line_item = ANY(%s)
            ORDER  BY f.fiscal_year DESC, f.line_item
            """,
            (args.as_of, args.ref, args.items.split(",")),
        )
        rows = cur.fetchall()
    print(f"as of {args.as_of}  ({len(rows)} facts)")
    for fy, item, currency, value, known, ref in rows:
        print(f"  FY{fy}  {item:18s} {currency} {value:>18,.0f}   known {known}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="investment-intelligence")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed").set_defaults(func=cmd_seed)

    bf = sub.add_parser("backfill")
    bf.add_argument("--since", default="2015-01-01")
    bf.add_argument("--until", default="2030-01-01")
    bf.add_argument("--limit", type=int, default=None,
                    help="bound this invocation; resume by running again")
    bf.set_defaults(func=cmd_backfill)

    pr = sub.add_parser("prices", help="local research only -- see docs/03-data-sources.md §7")
    pr.add_argument("--since", default="2015-01-01")
    pr.add_argument("--until", default="2030-01-01")
    pr.set_defaults(func=cmd_prices)

    fac = sub.add_parser("factors", help="compute the factor score, local research only")
    fac.add_argument("--as-of", default="today", dest="as_of")
    fac.set_defaults(func=cmd_factors)

    tech = sub.add_parser("technicals", help="compute technical indicators, local research only")
    tech.add_argument("--as-of", default="today", dest="as_of")
    tech.set_defaults(func=cmd_technicals)

    rk = sub.add_parser("risk", help="compute risk metrics, local research only")
    rk.add_argument("--as-of", default="today", dest="as_of")
    rk.set_defaults(func=cmd_risk)

    comb = sub.add_parser("combined", help="compute the combined fundamental+technical score")
    comb.add_argument("--as-of", default="today", dest="as_of")
    comb.set_defaults(func=cmd_combined)

    sz = sub.add_parser("size", help="split an amount across the top-ranked instruments")
    sz.add_argument("--amount", required=True, help="e.g. 5000")
    sz.add_argument("--currency", default="USD")
    sz.add_argument("--top", type=int, default=5, help="how many top-ranked instruments to include")
    sz.set_defaults(func=cmd_size)

    sub.add_parser("sectors", help="fetch SIC/sector classification (not local-only)").set_defaults(
        func=cmd_sectors)

    mc = sub.add_parser("marketcap", help="compute market cap, local research only")
    mc.add_argument("--as-of", default="today", dest="as_of")
    mc.set_defaults(func=cmd_marketcap)

    bt = sub.add_parser("backtest", help="validate the combined score against actual forward returns")
    bt.add_argument("--since", default="2017-01-01")
    bt.add_argument("--until", default="2026-03-01",
                    help="last checkpoint -- must be forward-days before today, or the "
                         "final points will not have a resolved forward return")
    bt.add_argument("--every-days", type=int, default=180, dest="every_days")
    bt.add_argument("--forward-days", type=int, default=180, dest="forward_days")
    bt.set_defaults(func=cmd_backtest)

    inc = sub.add_parser("incremental")
    inc.add_argument("--lookback", type=int, default=2,
                     help="years back to look for newly filed documents")
    inc.set_defaults(func=cmd_incremental)

    sch = sub.add_parser("schedule")
    sch.add_argument("--max-age-days", type=int, default=3, dest="max_age_days")
    sch.set_defaults(func=cmd_schedule)

    sub.add_parser("health").set_defaults(func=cmd_health)

    sub.add_parser("status").set_defaults(func=cmd_status)

    qual = sub.add_parser("quality")
    qual.add_argument("--limit", type=int, default=20)
    qual.set_defaults(func=cmd_quality)

    sub.add_parser("gaps").set_defaults(func=cmd_gaps)

    show = sub.add_parser("show")
    show.add_argument("--ref", required=True, help="SEC CIK")
    show.add_argument("--as-of", default="now", dest="as_of")
    show.add_argument("--items", default="REVENUE,NET_PROFIT,TOTAL_ASSETS,TOTAL_EQUITY")
    show.set_defaults(func=cmd_show)

    args = parser.parse_args(argv)
    # JSON to stdout, so a CI log is queryable three weeks later.
    structured.configure()
    args.func(args)


if __name__ == "__main__":
    main()
