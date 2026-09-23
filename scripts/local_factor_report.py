"""Renders the factor scores as a static HTML page, for local viewing only.

Deliberately NOT a route inside web/. The Next.js app is what gets deployed
to Vercel, and putting this inside it -- even behind a flag -- means one
misconfigured environment variable away from the public site rendering
Yahoo-sourced price data, which is exactly the exposure
docs/03-data-sources.md §7 documents and migration 024 seeds
redistributable=false specifically to avoid. Generating a plain file outside
web/ makes that mistake structurally impossible rather than merely unlikely:
there is no deploy step that touches this directory at all.

Shows two scores side by side, not one: the fundamental-only score
(factor_score.py, 3 factors) and the combined score (combined_score.py, 5
factors, two of them technical). The point of building the combination was
that it visibly changes the ranking -- MMYT drops from #2 to #5 once weak
technicals are blended in -- and showing only the combined score would hide
that the blend did anything at all.

Usage:
    PYTHONPATH=src python scripts/local_factor_report.py
    open local-only/factor_report.html
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import psycopg

from investment_intelligence.analytics import backtest, combined_score, factor_score
from investment_intelligence.observability import analytics_runs

OUT_DIR = Path(__file__).parent.parent / "local-only"
OUT_FILE = OUT_DIR / "factor_report.html"

DSN = os.environ.get("DATABASE_URL", "postgresql:///ii_dev")

# Parameterized by model_version -- the report shows two models, and a query
# that ignored model_version would silently mix rows from both together,
# which is exactly the bug the writer side of this had until today.
SCORES_QUERY = """
    SELECT i.value AS ticker, f.composite_score, f.factors, f.as_of
    FROM factor_scores f
    JOIN instrument_external_ids i
      ON i.instrument_id = f.instrument_id AND i.scheme = 'US_TICKER'
    WHERE f.model_version = %s
      AND f.as_of = (SELECT max(as_of) FROM factor_scores WHERE model_version = %s)
    ORDER BY f.composite_score DESC
"""

SECTORS_QUERY = """
    SELECT i.value AS ticker, sc.sector, sc.sic_description
    FROM sector_classifications sc
    JOIN instrument_external_ids i
      ON i.instrument_id = sc.instrument_id AND i.scheme = 'US_TICKER'
"""

MARKET_CAP_QUERY = """
    SELECT i.value AS ticker, m.market_cap, m.currency
    FROM market_cap_snapshots m
    JOIN instrument_external_ids i
      ON i.instrument_id = m.instrument_id AND i.scheme = 'US_TICKER'
    WHERE m.as_of = (SELECT max(as_of) FROM market_cap_snapshots)
"""

TECHNICALS_QUERY = """
    SELECT i.value AS ticker, t.indicators
    FROM technical_indicators t
    JOIN instrument_external_ids i
      ON i.instrument_id = t.instrument_id AND i.scheme = 'US_TICKER'
    WHERE t.as_of = (SELECT max(as_of) FROM technical_indicators)
"""

RISK_QUERY = """
    SELECT i.value AS ticker, r.metrics
    FROM risk_metrics r
    JOIN instrument_external_ids i
      ON i.instrument_id = r.instrument_id AND i.scheme = 'US_TICKER'
    WHERE r.as_of = (SELECT max(as_of) FROM risk_metrics)
"""


def _factor_row(label: str, comp: dict | None) -> str:
    if comp is None:
        return f'<tr class="missing"><td>{label}</td><td colspan="2">not available</td></tr>'
    value = float(comp["value"])
    rank = comp["rank"]
    return (f'<tr><td>{label}</td>'
            f'<td class="num">{value:+.4f}</td>'
            f'<td><div class="bar"><div class="fill" style="width:{rank*100:.1f}%"></div>'
            f'<span>{rank:.2f}</span></div></td></tr>')


FACTOR_LABELS = {
    "momentum": "Momentum (6mo price return)",
    "quality_net_margin": "Quality (net margin)",
    "growth_revenue": "Growth (revenue YoY)",
    "trend_strength": "Trend (price vs SMA-200)",
    "macd_momentum": "MACD momentum (histogram)",
}


def _technicals_block(ind: dict | None) -> str:
    if ind is None:
        return '<div class="tech-missing">no technical indicators computed</div>'
    rsi = ind.get("rsi_14")
    cross = ind.get("golden_cross")
    cross_s = ("golden cross" if cross is True else
               "death cross" if cross is False else "-")
    bo = ind.get("range_20d", {}).get("breakout", "-")
    pb = ind.get("bollinger", {}).get("percent_b")
    macd = ind.get("macd")
    parts = []
    if rsi is not None:
        parts.append(f"RSI(14) {float(rsi):.1f} <i>(shown, not blended -- see combined_score.py)</i>")
    parts.append(f"{cross_s} (SMA50/SMA200)")
    if macd:
        parts.append(f"MACD hist {float(macd['histogram']):+.3f}")
    if pb is not None:
        parts.append(f"Bollinger %B {float(pb):.2f}")
    parts.append(f"20d range: {bo}")
    return '<div class="tech">' + ' &middot; '.join(parts) + '</div>'


def _meta_line(ticker: str, sector_by_ticker: dict[str, tuple], cap_by_ticker: dict[str, tuple],
               risk_by_ticker: dict[str, dict]) -> str:
    parts = []
    if ticker in sector_by_ticker:
        sector, sic_desc = sector_by_ticker[ticker]
        parts.append(f"{sector} <i>({sic_desc})</i>")
    if ticker in cap_by_ticker:
        cap, currency = cap_by_ticker[ticker]
        parts.append(f"mkt cap {currency} {float(cap):,.0f}")
    if ticker in risk_by_ticker:
        m = risk_by_ticker[ticker]
        if "annualized_volatility" in m:
            parts.append(f"ann.vol {float(m['annualized_volatility']):.0%}")
        if "max_drawdown" in m:
            parts.append(f"max DD {float(m['max_drawdown']):.0%}")
    if not parts:
        return ""
    return f'<div class="meta">{" &middot; ".join(parts)}</div>'


def _section(title: str, subtitle: str, rows: list[tuple], factor_names: list[str],
             technicals_by_ticker: dict[str, dict], show_technicals: bool,
             sector_by_ticker: dict[str, tuple], cap_by_ticker: dict[str, tuple],
             risk_by_ticker: dict[str, dict]) -> str:
    if not rows:
        return f"<h2>{title}</h2><p class='note'>no scores computed yet</p>"

    full = [r for r in rows if r[2]["factors_available"] == r[2].get("factors_total", 3)]
    partial = [r for r in rows if r[2]["factors_available"] < r[2].get("factors_total", 3)]

    def card(ticker, composite, factors) -> str:
        c = factors["components"]
        body = "".join(_factor_row(FACTOR_LABELS[name], c.get(name)) for name in factor_names)
        total = factors.get("factors_total", 3)
        tech = _technicals_block(technicals_by_ticker.get(ticker)) if show_technicals else ""
        meta = _meta_line(ticker, sector_by_ticker, cap_by_ticker, risk_by_ticker)
        return f"""
        <details class="card">
          <summary>
            <span class="ticker">{ticker}</span>
            <span class="score">{float(composite):.3f}</span>
            <span class="avail">{factors['factors_available']}/{total} factors</span>
          </summary>
          {meta}
          <table><tbody>{body}</tbody></table>
          {tech}
        </details>"""

    full_html = "".join(card(t, s, f) for t, s, f, _ in full)
    partial_html = "".join(card(t, s, f) for t, s, f, _ in partial)
    total = rows[0][2].get("factors_total", 3)
    partial_section = f"""
      <p class="note">Not ranked below -- incomplete factor coverage, not
         comparable to the ranked list above.</p>
      {partial_html}""" if partial else ""

    return f"""
      <h2>{title}</h2>
      <div class="sub2">{subtitle}</div>
      <p class="note">Ranked: {len(full)} of {len(rows)} tracked instruments, all {total} factors available.</p>
      {full_html}
      {partial_section}"""


JOB_LABELS = {
    "TECHNICALS": "technicals", "RISK": "risk", "COMBINED_SCORE": "combined",
    "SECTORS": "sectors", "MARKET_CAP": "marketcap", "BACKTEST": "backtest",
}


def _pipeline_block(last_success: dict[str, dict | None], recent_failures: list[dict]) -> str:
    """Per-job last-success line plus any recent failure, from
    `analytics_runs` (migration 031) -- the run log `ingestion_runs` never
    covered because this pipeline is recomputation, not source ingestion.
    See observability/analytics_runs.py's module docstring."""
    rows = []
    for job in analytics_runs.JOBS:
        row = last_success.get(job)
        label = JOB_LABELS.get(job, job.lower())
        if row is None:
            rows.append(f'<tr class="missing"><td>{label}</td>'
                        f'<td colspan="2">never succeeded</td></tr>')
            continue
        as_of = row["as_of"].isoformat() if row["as_of"] else "-"
        rows.append(f'<tr><td>{label}</td><td>{row["finished_at"]:%Y-%m-%d %H:%M}</td>'
                    f'<td class="num">as_of {as_of} &middot; wrote {row["rows_written"]}</td></tr>')

    failures_html = ""
    if recent_failures:
        items = "".join(
            f'<li><b>{JOB_LABELS.get(r["job"], r["job"].lower())}</b> '
            f'({r["started_at"]:%Y-%m-%d %H:%M}): {r["error"]}</li>'
            for r in recent_failures
        )
        failures_html = f'<div class="pipeline-warn">Recent failures:<ul>{items}</ul></div>'

    return f"""
      <table class="pipeline"><tbody>{''.join(rows)}</tbody></table>
      {failures_html}"""


def _backtest_block(summary: dict | None) -> str:
    if summary is None or summary.get("n", 0) == 0:
        return '<p class="note">No backtest run yet -- `make backtest`.</p>'
    if summary["ic"] is None:
        return f'<p class="note">{summary["note"]}</p>'
    ic = summary["ic"]
    colour = "#7ee787" if ic > 0.1 else "#e77e7e" if ic < -0.1 else "#999"
    return f"""
      <div class="ic-box">
        <span class="ic-value" style="color:{colour}">{ic:+.3f}</span>
        <span class="ic-label">information coefficient</span>
      </div>
      <p class="note">{summary['note']}</p>"""


def render(fundamental_rows: list[tuple], combined_rows: list[tuple],
           technicals_by_ticker: dict[str, dict], sector_by_ticker: dict[str, tuple],
           cap_by_ticker: dict[str, tuple], backtest_summary: dict | None,
           risk_by_ticker: dict[str, dict], pipeline_last_success: dict[str, dict | None],
           pipeline_recent_failures: list[dict]) -> str:
    as_of = (combined_rows or fundamental_rows or [(None, None, None, date.today())])[0][3]

    fundamental_section = _section(
        "Fundamental-only score", "model factor-v1-rank &middot; momentum, quality, growth",
        fundamental_rows, ["momentum", "quality_net_margin", "growth_revenue"],
        technicals_by_ticker, show_technicals=False,
        sector_by_ticker=sector_by_ticker, cap_by_ticker=cap_by_ticker,
        risk_by_ticker=risk_by_ticker)
    combined_section = _section(
        "Combined score (fundamental + technical)",
        "model combined-v1-rank &middot; adds trend strength and MACD momentum "
        "to the three factors above",
        combined_rows,
        ["momentum", "quality_net_margin", "growth_revenue", "trend_strength", "macd_momentum"],
        technicals_by_ticker, show_technicals=True,
        sector_by_ticker=sector_by_ticker, cap_by_ticker=cap_by_ticker,
        risk_by_ticker=risk_by_ticker)

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>Investment scores -- local research only</title>
<style>
  body {{ background:#0d0f12; color:#e6e6e6; font:14px -apple-system,sans-serif;
          max-width:780px; margin:40px auto; padding:0 20px; }}
  .banner {{ background:#3a2a08; border:1px solid #a66a00; color:#ffcc66;
             padding:14px 18px; border-radius:6px; margin-bottom:28px;
             font-size:13px; line-height:1.5; }}
  .banner b {{ color:#ffdd88; }}
  h1 {{ font-size:20px; margin-bottom:4px; }}
  .as-of {{ color:#888; font-size:13px; margin-bottom:8px; }}
  h2 {{ font-size:16px; margin-top:40px; color:#eee; border-top:1px solid #2a2e34;
        padding-top:24px; }}
  .sub2 {{ color:#888; font-size:12.5px; margin-bottom:10px; }}
  .note {{ color:#999; font-size:12.5px; }}
  .card {{ background:#16191d; border:1px solid #2a2e34; border-radius:6px;
           margin-bottom:8px; padding:0; }}
  .card summary {{ cursor:pointer; padding:12px 16px; display:flex;
                    align-items:center; gap:14px; list-style:none; }}
  .card summary::-webkit-details-marker {{ display:none; }}
  .ticker {{ font-weight:600; width:60px; }}
  .score {{ color:#7ee787; font-variant-numeric:tabular-nums; width:60px; }}
  .avail {{ color:#666; font-size:12px; margin-left:auto; }}
  table {{ width:100%; border-collapse:collapse; }}
  td {{ padding:8px 16px; border-top:1px solid #22262c; font-size:13px; }}
  td.num {{ font-variant-numeric:tabular-nums; color:#aaa; width:90px; }}
  .bar {{ position:relative; background:#22262c; border-radius:3px; height:16px; }}
  .bar .fill {{ background:#3f7a3f; height:100%; border-radius:3px; }}
  .bar span {{ position:absolute; right:6px; top:0; font-size:11px; color:#ccc; }}
  tr.missing td {{ color:#666; font-style:italic; }}
  .tech {{ padding:10px 16px; border-top:1px solid #22262c; font-size:12.5px;
           color:#9db4d1; }}
  .tech i {{ color:#666; font-style:italic; }}
  .tech-missing {{ padding:10px 16px; border-top:1px solid #22262c;
                    font-size:12.5px; color:#666; font-style:italic; }}
  .meta {{ padding:0 16px 10px; font-size:12px; color:#8a8a8a; }}
  .meta i {{ color:#666; font-style:italic; }}
  .ic-box {{ display:flex; align-items:baseline; gap:10px; margin:14px 0 6px; }}
  .ic-value {{ font-size:28px; font-weight:700; font-variant-numeric:tabular-nums; }}
  .ic-label {{ color:#999; font-size:13px; }}
  table.pipeline {{ width:100%; border-collapse:collapse; font-size:12.5px; }}
  table.pipeline td {{ padding:6px 10px; border-top:1px solid #22262c; }}
  table.pipeline td:first-child {{ color:#eee; font-weight:600; width:110px; }}
  table.pipeline td.num {{ color:#888; text-align:right; }}
  table.pipeline tr.missing td {{ color:#c77; font-style:italic; }}
  .pipeline-warn {{ margin-top:10px; padding:10px 14px; background:#3a1414;
                     border:1px solid #a63a3a; border-radius:6px; color:#f0a; font-size:12.5px; }}
  .pipeline-warn ul {{ margin:6px 0 0 18px; padding:0; }}
</style></head>
<body>
  <div class="banner">
    <b>LOCAL RESEARCH ONLY -- not deployed, not public.</b><br>
    Price data is sourced via yfinance, an unofficial client against Yahoo's
    internal endpoints. Yahoo's own terms restrict redistributing that data
    for a public, customer-facing product -- see
    <code>docs/03-data-sources.md §7</code>. This page is generated to a file
    outside <code>web/</code>, which the deployed app never reads, so it
    cannot end up served publicly by mistake. Every score below is a
    calculation, not a recommendation -- see
    <code>docs/01-vision-and-scope.md</code>'s own test for what that means.
  </div>
  <h1>Investment scores</h1>
  <div class="as-of">as of {as_of}</div>

  <h2>Pipeline -- did each job actually run</h2>
  <div class="sub2">last successful run per job, from analytics_runs
    (migration 031) &middot; run <code>make analytics-status</code> for full history</div>
  {_pipeline_block(pipeline_last_success, pipeline_recent_failures)}

  <h2>Backtest -- does the combined score actually predict anything?</h2>
  <div class="sub2">rank correlation between composite_score and actual forward
    return, pooled across every historical checkpoint &middot; see
    analytics/backtest.py for method and caveats</div>
  {_backtest_block(backtest_summary)}

  {fundamental_section}
  {combined_section}
</body></html>"""


BACKTEST_QUERY = """
    SELECT instrument_id, as_of, model_version, composite_score, forward_days, forward_return
    FROM backtest_results WHERE model_version = %s
"""


def main() -> None:
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(SCORES_QUERY, (factor_score.MODEL_VERSION, factor_score.MODEL_VERSION))
            fundamental_rows = cur.fetchall()
            cur.execute(SCORES_QUERY, (combined_score.MODEL_VERSION, combined_score.MODEL_VERSION))
            combined_rows = cur.fetchall()
            cur.execute(TECHNICALS_QUERY)
            technicals_by_ticker = dict(cur.fetchall())
            cur.execute(SECTORS_QUERY)
            sector_by_ticker = {t: (sector, sic) for t, sector, sic in cur.fetchall()}
            cur.execute(MARKET_CAP_QUERY)
            cap_by_ticker = {t: (cap, currency) for t, cap, currency in cur.fetchall()}
            cur.execute(RISK_QUERY)
            risk_by_ticker = dict(cur.fetchall())
            cur.execute(BACKTEST_QUERY, (backtest.MODEL_VERSION,))
            backtest_points = [
                backtest.BacktestPoint(instrument_id=iid, as_of=as_of, composite_score=score,
                                       forward_days=fd, forward_return=fr)
                for iid, as_of, _mv, score, fd, fr in cur.fetchall()
            ]
            backtest_summary = backtest.information_coefficient(backtest_points)

            pipeline_last_success = analytics_runs.last_success(conn)
            pipeline_recent_failures = [
                r for r in analytics_runs.recent(conn, limit=20) if r["outcome"] == "FAILED"
            ]

    if not fundamental_rows and not combined_rows:
        raise SystemExit("no factor_scores rows -- run `make factors` and `make combined` first")

    OUT_DIR.mkdir(exist_ok=True)
    OUT_FILE.write_text(render(fundamental_rows, combined_rows, technicals_by_ticker,
                               sector_by_ticker, cap_by_ticker, backtest_summary,
                               risk_by_ticker, pipeline_last_success, pipeline_recent_failures))
    print(f"wrote {OUT_FILE} ({len(fundamental_rows)} fundamental-only, "
          f"{len(combined_rows)} combined, {len(technicals_by_ticker)} with technicals, "
          f"{len(sector_by_ticker)} with sector, {len(cap_by_ticker)} with market cap, "
          f"{len(risk_by_ticker)} with risk metrics)")


if __name__ == "__main__":
    main()
