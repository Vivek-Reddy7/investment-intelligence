"""Renders the factor scores as a static HTML page, for local viewing only.

Deliberately NOT a route inside web/. The Next.js app is what gets deployed
to Vercel, and putting this inside it -- even behind a flag -- means one
misconfigured environment variable away from the public site rendering
Yahoo-sourced price data, which is exactly the exposure
docs/03-data-sources.md §7 documents and migration 024 seeds
redistributable=false specifically to avoid. Generating a plain file outside
web/ makes that mistake structurally impossible rather than merely unlikely:
there is no deploy step that touches this directory at all.

Usage:
    PYTHONPATH=src python scripts/local_factor_report.py
    open local-only/factor_report.html
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import psycopg

OUT_DIR = Path(__file__).parent.parent / "local-only"
OUT_FILE = OUT_DIR / "factor_report.html"

DSN = os.environ.get("DATABASE_URL", "postgresql:///ii_dev")

QUERY = """
    SELECT i.value AS ticker, f.composite_score, f.factors, f.as_of
    FROM factor_scores f
    JOIN instrument_external_ids i
      ON i.instrument_id = f.instrument_id AND i.scheme = 'US_TICKER'
    WHERE f.as_of = (SELECT max(as_of) FROM factor_scores)
    ORDER BY f.composite_score DESC
"""

TECHNICALS_QUERY = """
    SELECT i.value AS ticker, t.indicators
    FROM technical_indicators t
    JOIN instrument_external_ids i
      ON i.instrument_id = t.instrument_id AND i.scheme = 'US_TICKER'
    WHERE t.as_of = (SELECT max(as_of) FROM technical_indicators)
"""


def _factor_row(name: str, label: str, comp: dict | None) -> str:
    if comp is None:
        return f'<tr class="missing"><td>{label}</td><td colspan="2">not available</td></tr>'
    value = float(comp["value"])
    rank = comp["rank"]
    return (f'<tr><td>{label}</td>'
            f'<td class="num">{value:+.4f}</td>'
            f'<td><div class="bar"><div class="fill" style="width:{rank*100:.1f}%"></div>'
            f'<span>{rank:.2f}</span></div></td></tr>')


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
        parts.append(f"RSI(14) {float(rsi):.1f}")
    parts.append(f"{cross_s} (SMA50/SMA200)")
    if macd:
        parts.append(f"MACD hist {float(macd['histogram']):+.3f}")
    if pb is not None:
        parts.append(f"Bollinger %B {float(pb):.2f}")
    parts.append(f"20d range: {bo}")
    return '<div class="tech">' + ' &middot; '.join(parts) + '</div>'


def render(rows: list[tuple], technicals_by_ticker: dict[str, dict]) -> str:
    full = [r for r in rows if r[2]["factors_available"] == 3]
    partial = [r for r in rows if r[2]["factors_available"] < 3]
    as_of = rows[0][3] if rows else date.today()

    def card(ticker, composite, factors) -> str:
        c = factors["components"]
        body = "".join([
            _factor_row("momentum", "Momentum (6mo price return)", c.get("momentum")),
            _factor_row("quality", "Quality (net margin)", c.get("quality_net_margin")),
            _factor_row("growth", "Growth (revenue YoY)", c.get("growth_revenue")),
        ])
        tech = _technicals_block(technicals_by_ticker.get(ticker))
        return f"""
        <details class="card">
          <summary>
            <span class="ticker">{ticker}</span>
            <span class="score">{float(composite):.3f}</span>
            <span class="avail">{factors['factors_available']}/3 factors</span>
          </summary>
          <table><tbody>{body}</tbody></table>
          {tech}
        </details>"""

    full_html = "".join(card(t, s, f) for t, s, f, _ in full)
    partial_html = "".join(card(t, s, f) for t, s, f, _ in partial)
    partial_section = f"""
      <h2>Not ranked -- incomplete factor coverage</h2>
      <p class="note">Fewer than 3 factors available. Not comparable to the
         ranked list above; shown separately rather than interleaved into
         one misleadingly total order.</p>
      {partial_html}""" if partial else ""

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>Factor scores -- local research only</title>
<style>
  body {{ background:#0d0f12; color:#e6e6e6; font:14px -apple-system,sans-serif;
          max-width:760px; margin:40px auto; padding:0 20px; }}
  .banner {{ background:#3a2a08; border:1px solid #a66a00; color:#ffcc66;
             padding:14px 18px; border-radius:6px; margin-bottom:28px;
             font-size:13px; line-height:1.5; }}
  .banner b {{ color:#ffdd88; }}
  h1 {{ font-size:20px; margin-bottom:4px; }}
  .sub {{ color:#888; font-size:13px; margin-bottom:24px; }}
  h2 {{ font-size:15px; margin-top:36px; color:#ccc; }}
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
  .tech-missing {{ padding:10px 16px; border-top:1px solid #22262c;
                    font-size:12.5px; color:#666; font-style:italic; }}
</style></head>
<body>
  <div class="banner">
    <b>LOCAL RESEARCH ONLY -- not deployed, not public.</b><br>
    Price data is sourced via yfinance, an unofficial client against Yahoo's
    internal endpoints. Yahoo's own terms restrict redistributing that data
    for a public, customer-facing product -- see
    <code>docs/03-data-sources.md §7</code>. This page is generated to a file
    outside <code>web/</code>, which the deployed app never reads, so it
    cannot end up served publicly by mistake.
  </div>
  <h1>Factor scores</h1>
  <div class="sub">as of {as_of} &middot; model factor-v1-rank &middot;
    equal-weighted percentile rank, not a trained model -- see
    analytics/factor_score.py for why</div>
  <h2>Ranked ({len(full)} of {len(rows)} tracked instruments, all 3 factors available)</h2>
  {full_html}
  {partial_section}
</body></html>"""


def main() -> None:
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(QUERY)
            rows = cur.fetchall()
            cur.execute(TECHNICALS_QUERY)
            technicals_by_ticker = dict(cur.fetchall())

    if not rows:
        raise SystemExit("no factor_scores rows -- run `investment-intelligence factors` first")

    OUT_DIR.mkdir(exist_ok=True)
    OUT_FILE.write_text(render(rows, technicals_by_ticker))
    print(f"wrote {OUT_FILE} ({len(rows)} instruments, "
          f"{len(technicals_by_ticker)} with technicals)")


if __name__ == "__main__":
    main()
