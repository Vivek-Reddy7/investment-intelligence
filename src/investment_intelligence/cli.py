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
from investment_intelligence.ingest import backfill, incremental
from investment_intelligence.ingest import rejections as rejection_log
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

    inc = sub.add_parser("incremental")
    inc.add_argument("--lookback", type=int, default=2,
                     help="years back to look for newly filed documents")
    inc.set_defaults(func=cmd_incremental)

    sch = sub.add_parser("schedule")
    sch.add_argument("--max-age-days", type=int, default=3, dest="max_age_days")
    sch.set_defaults(func=cmd_schedule)

    sub.add_parser("health").set_defaults(func=cmd_health)

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
    args.func(args)


if __name__ == "__main__":
    main()
