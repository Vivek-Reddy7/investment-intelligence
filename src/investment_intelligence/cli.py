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

from investment_intelligence.companies import STAGE_ONE
from investment_intelligence.db import connect, migrate
from investment_intelligence.ingest import backfill
from investment_intelligence.sources.edgar import EdgarSource

SOURCE_ID = "SEC_EDGAR"
JOB = "edgar-stage-one"

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
    if report.rejections:
        print(f"  rejections={len(report.rejections)}; first few:")
        for rejection in report.rejections[:5]:
            print(f"    {rejection.instrument_ref}: {rejection.reason}")


def cmd_gaps(args: argparse.Namespace) -> None:
    with connect() as conn:
        found = backfill.gaps(conn, JOB, "SEC_CIK")
    if not found:
        print("no gaps: every tracked instrument is DONE")
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

    sub.add_parser("gaps").set_defaults(func=cmd_gaps)

    show = sub.add_parser("show")
    show.add_argument("--ref", required=True, help="SEC CIK")
    show.add_argument("--as-of", default="now", dest="as_of")
    show.add_argument("--items", default="REVENUE,NET_PROFIT,TOTAL_ASSETS,TOTAL_EQUITY")
    show.set_defaults(func=cmd_show)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
