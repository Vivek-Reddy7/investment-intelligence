"""Runs `paper-trader`'s SMA-crossover strategy backtest over the current
top-ranked (combined score) basket, one subprocess call per ticker.

This is a CLI-to-CLI bridge, not a library import. investment-intelligence's
README says, deliberately: "paper-trader ... [is a] separate, standalone
project ... deliberately separate codebases with separate lifecycles.
Neither imports the other." docs/backlog.md's "consume paper-trader as a
library" entry is answered here in the spirit of what it was actually after
-- using paper-trader's backtest engine against a basket this project
screened -- without reversing that boundary: this script never imports
`paper_trader`, never touches its SQLite store or Python types directly, and
paper-trader has no packaging metadata to import as a library even if that
boundary were reversed (no pyproject.toml/setup.py -- confirmed before
writing this). Each project keeps fetching, storing and testing its own
data; the only thing crossing the line is two subprocess calls and some text
parsing of paper-trader's own printed report, the same interface anyone
running paper-trader by hand already reads.

Genuinely different question from analytics/backtest.py's own backtest:
that one asks "does a higher combined-score rank correlate with a better
forward return" (an information-coefficient study, no trading involved).
This asks "if I ran a concrete trading strategy -- with realistic costs --
on each of today's top-ranked names, what would have actually happened."
Complementary, not a duplicate.

LOCAL RESEARCH ONLY, same posture as scripts/local_factor_report.py:
paper-trader's own data is yfinance-sourced (docs/03-data-sources.md §7),
and this script lives outside src/ and web/ so there is no deploy path that
could ever expose it.

Usage:
    PYTHONPATH=src python scripts/local_basket_backtest.py
    PYTHONPATH=src python scripts/local_basket_backtest.py --top 5 --since 2023-01-01

Needs a sibling `paper-trader` checkout (default: ../paper-trader relative
to this repo, override with PAPER_TRADER_DIR) with its own venv already set
up per its own README (`python3 -m venv .venv && pip install -r
requirements.txt`).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import psycopg

from investment_intelligence.analytics import combined_score

REPO_ROOT = Path(__file__).parent.parent
DEFAULT_PAPER_TRADER_DIR = REPO_ROOT.parent / "paper-trader"

DSN = os.environ.get("DATABASE_URL", "postgresql:///ii_dev")

TOP_RANKED_QUERY = """
    SELECT i.value AS ticker
    FROM factor_scores f
    JOIN instrument_external_ids i
      ON i.instrument_id = f.instrument_id AND i.scheme = 'US_TICKER'
    WHERE f.model_version = %s
      AND f.as_of = (SELECT max(as_of) FROM factor_scores WHERE model_version = %s)
      AND (f.factors->>'factors_available')::int = (f.factors->>'factors_total')::int
    ORDER BY f.composite_score DESC
    LIMIT %s
"""

# Matches the exact lines cmd_backtest() in paper-trader/src/paper_trader/cli.py
# prints -- this script is reading paper-trader's own report text, the same
# thing a person running it by hand would read.
_METRIC_PATTERNS = {
    "total_return_pct": re.compile(r"^total return\s+(-?[\d.]+)%", re.MULTILINE),
    "buy_and_hold_return_pct": re.compile(r"^buy & hold\s+(-?[\d.]+)%", re.MULTILINE),
    "max_drawdown_pct": re.compile(r"^max drawdown\s+(-?[\d.]+)%", re.MULTILINE),
    "n_trades": re.compile(r"^trades\s+(\d+)", re.MULTILINE),
}


class PaperTraderUnavailable(RuntimeError):
    pass


def _paper_trader_python(paper_trader_dir: Path) -> Path:
    py = paper_trader_dir / ".venv" / "bin" / "python"
    if not py.exists():
        raise PaperTraderUnavailable(
            f"no venv at {paper_trader_dir}/.venv -- set up paper-trader first "
            f"(its own README: python3 -m venv .venv && pip install -r requirements.txt), "
            f"or point PAPER_TRADER_DIR at a checkout that already has one"
        )
    return py


def top_ranked_tickers(conn: psycopg.Connection, top: int) -> list[str]:
    """The same fully-covered, ranked-by-composite-score basket `cli.py`'s
    `size` command sizes an allocation across -- this tool answers a
    different question about the same basket, so it reuses the same
    selection."""
    with conn.cursor() as cur:
        cur.execute(TOP_RANKED_QUERY,
                    (combined_score.MODEL_VERSION, combined_score.MODEL_VERSION, top))
        return [row[0] for row in cur.fetchall()]


def _run_paper_trader(paper_trader_dir: Path, py: Path, args: list[str]) -> subprocess.CompletedProcess:
    # PYTHONPATH=src matches paper-trader's own pytest.ini -- `paper_trader`
    # has no packaging metadata, so nothing makes it importable without this.
    env = {**os.environ, "PYTHONPATH": "src"}
    return subprocess.run(
        [str(py), "-m", "paper_trader.cli", *args],
        cwd=paper_trader_dir, env=env, capture_output=True, text=True, timeout=120,
    )


def backtest_one(paper_trader_dir: Path, py: Path, ticker: str, since: date, until: date,
                  fast: int, slow: int, cash: float, commission: float,
                  slippage: float, report_dir: Path | None) -> dict:
    """Ingest then backtest one ticker through paper-trader's own CLI.
    Returns a dict of parsed metrics, or {"error": ...} if either step
    failed -- one ticker failing (e.g. a symbol paper-trader's yfinance
    adapter cannot resolve) must not stop the rest of the basket, the same
    discipline ingest/incremental.py already applies per-instrument."""
    db = str(paper_trader_dir / "market.db")
    ingest = _run_paper_trader(
        paper_trader_dir, py,
        ["--db", db, "ingest", ticker, "--start", since.isoformat(), "--end", until.isoformat()],
    )
    if ingest.returncode != 0:
        return {"ticker": ticker, "error": f"ingest failed: {ingest.stderr.strip() or ingest.stdout.strip()}"}
    if "no bars returned" in ingest.stdout:
        return {"ticker": ticker, "error": "paper-trader's data source returned no bars"}

    bt_args = ["--db", db, "backtest", ticker,
               "--start", since.isoformat(), "--end", until.isoformat(),
               "--fast", str(fast), "--slow", str(slow), "--cash", str(cash),
               "--commission", str(commission), "--slippage", str(slippage)]
    if report_dir is not None:
        bt_args += ["--report", str(report_dir / f"{ticker}.html")]
    backtest = _run_paper_trader(paper_trader_dir, py, bt_args)
    if backtest.returncode != 0:
        return {"ticker": ticker,
                "error": f"backtest failed: {backtest.stderr.strip() or backtest.stdout.strip()}"}

    out = backtest.stdout
    result: dict = {"ticker": ticker}
    for key, pattern in _METRIC_PATTERNS.items():
        m = pattern.search(out)
        if m is None:
            return {"ticker": ticker, "error": f"could not parse '{key}' from paper-trader's output"}
        result[key] = int(m.group(1)) if key == "n_trades" else float(m.group(1))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--top", type=int, default=5,
                        help="how many top-ranked (combined score) tickers to backtest")
    parser.add_argument("--since", type=date.fromisoformat,
                        default=date.today() - timedelta(days=730))
    parser.add_argument("--until", type=date.fromisoformat, default=date.today())
    parser.add_argument("--fast", type=int, default=20, help="paper-trader SMA fast window")
    parser.add_argument("--slow", type=int, default=50, help="paper-trader SMA slow window")
    parser.add_argument("--cash", type=float, default=100_000.0)
    parser.add_argument("--commission", type=float, default=0.03, metavar="PCT")
    parser.add_argument("--slippage", type=float, default=0.05, metavar="PCT")
    parser.add_argument("--report", action="store_true",
                        help="also write per-ticker HTML reports under local-only/basket_backtest/")
    args = parser.parse_args(argv)

    paper_trader_dir = Path(os.environ.get("PAPER_TRADER_DIR", str(DEFAULT_PAPER_TRADER_DIR)))
    if not paper_trader_dir.is_dir():
        print(f"no paper-trader checkout at {paper_trader_dir} -- clone "
              f"https://github.com/Vivek-Reddy7/paper-trader there, or set "
              f"PAPER_TRADER_DIR", file=sys.stderr)
        return 1
    try:
        py = _paper_trader_python(paper_trader_dir)
    except PaperTraderUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1

    report_dir = None
    if args.report:
        report_dir = REPO_ROOT / "local-only" / "basket_backtest"
        report_dir.mkdir(parents=True, exist_ok=True)

    with psycopg.connect(DSN) as conn:
        tickers = top_ranked_tickers(conn, args.top)

    if not tickers:
        print("no ranked, fully-covered instruments -- run `make combined` first", file=sys.stderr)
        return 1

    print(f"basket: top {len(tickers)} by combined score ({combined_score.MODEL_VERSION}), "
          f"{args.since} to {args.until}")
    print(f"strategy: SMA {args.fast}/{args.slow} crossover, {args.commission}% commission, "
          f"{args.slippage}% slippage (paper-trader)\n")

    results = [
        backtest_one(paper_trader_dir, py, ticker, args.since, args.until, args.fast, args.slow,
                    args.cash, args.commission, args.slippage, report_dir)
        for ticker in tickers
    ]

    ok = [r for r in results if "error" not in r]
    failed = [r for r in results if "error" in r]

    print(f"{'ticker':8s} {'strategy':>10s} {'buy&hold':>10s} {'max DD':>9s} {'trades':>7s}")
    for r in sorted(ok, key=lambda r: -r["total_return_pct"]):
        print(f"{r['ticker']:8s} {r['total_return_pct']:>9.2f}% {r['buy_and_hold_return_pct']:>9.2f}% "
              f"{r['max_drawdown_pct']:>8.2f}% {r['n_trades']:>7d}")

    if ok:
        beat = sum(1 for r in ok if r["total_return_pct"] > r["buy_and_hold_return_pct"])
        avg_strategy = sum(r["total_return_pct"] for r in ok) / len(ok)
        avg_hold = sum(r["buy_and_hold_return_pct"] for r in ok) / len(ok)
        print(f"\n{beat}/{len(ok)} beat buy-and-hold on their own history. "
              f"avg strategy return {avg_strategy:+.2f}% vs avg buy-and-hold {avg_hold:+.2f}%")
        print("(this basket's own history, one run, one strategy -- a demonstration of the "
              "bridge, not a claim this strategy works; see analytics/backtest.py's own "
              "small-sample caveat, which applies here at least as much)")

    if failed:
        print("\nfailed:")
        for r in failed:
            print(f"  {r['ticker']:8s} {r['error']}")

    if report_dir is not None and ok:
        print(f"\nper-ticker HTML reports: {report_dir}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
