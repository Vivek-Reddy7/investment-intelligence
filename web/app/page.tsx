/**
 * The screener. A server component: it calls the service layer directly rather
 * than fetching its own API over HTTP, which would be a pointless round trip.
 *
 * The as-of date input is the whole product made visible. Everything else here
 * is a conventional screener; that one field is the thing no free Indian tool
 * offers.
 */

import Link from "next/link";
import {
  currencies, fiscalYears, freshness, metricDefinitions, screen,
  type Criterion,
} from "@/lib/queries";
import { Provenance } from "./provenance";

export const dynamic = "force-dynamic";

function pct(value: string, unit: string) {
  const n = Number(value);
  return unit === "PERCENT" ? `${(n * 100).toFixed(1)}%` : n.toFixed(2);
}

export default async function Screener({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const one = (k: string) => {
    const v = params[k];
    return Array.isArray(v) ? v[0] : v;
  };

  const [metrics, years, curr, fresh] = await Promise.all([
    metricDefinitions(), fiscalYears(), currencies(), freshness(),
  ]);

  const fy = Number(one("fy") ?? years[0] ?? new Date().getFullYear());
  const currency = one("currency") ?? (curr.includes("USD") ? "USD" : curr[0] ?? "USD");
  const asOf = one("as_of") ?? "";

  // Two criteria in the form is enough to demonstrate multi-criteria screening
  // without turning the page into a query builder. The API takes up to eight.
  const criteria: Criterion[] = [];
  for (const i of [1, 2]) {
    const metric = one(`m${i}`);
    const op = one(`o${i}`) ?? "gte";
    const value = one(`v${i}`);
    if (metric && value) criteria.push({ metric, op, value });
  }

  let rows: Awaited<ReturnType<typeof screen>> = [];
  let error: string | null = null;
  if (criteria.length > 0) {
    try {
      rows = await screen({
        criteria, fiscalYear: fy, currency,
        asOf: asOf ? new Date(asOf + "T23:59:59Z").toISOString() : null,
      });
    } catch (err) {
      error = err instanceof Error ? err.message : "screen failed";
    }
  }

  const shown = criteria.map((c) => c.metric);
  const byCode = new Map(metrics.map((m) => [m.metric_code, m]));
  const worst = fresh.reduce<number | null>(
    (acc, f) => (f.stale_days === null ? acc : Math.max(acc ?? 0, f.stale_days)), null);

  return (
    <>
      <div className="disclaimer">
        <strong>Not investment advice.</strong> This tool presents figures companies
        filed and metrics computed from them. It makes no recommendation and
        names no target price. Every number links to the filing it came from.
      </div>

      <form className="screen" method="get">
        <div>
          <label htmlFor="fy">Fiscal year</label>
          <select id="fy" name="fy" defaultValue={String(fy)}>
            {years.map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="currency">Currency</label>
          <select id="currency" name="currency" defaultValue={currency}>
            {curr.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        {[1, 2].map((i) => (
          <div key={i} style={{ display: "flex", gap: 6, alignItems: "flex-end" }}>
            <div>
              <label htmlFor={`m${i}`}>Metric {i}</label>
              <select id={`m${i}`} name={`m${i}`} defaultValue={one(`m${i}`) ?? ""}>
                <option value="">—</option>
                {metrics.map((m) => (
                  <option key={m.metric_code} value={m.metric_code}>{m.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor={`o${i}`}>&nbsp;</label>
              <select id={`o${i}`} name={`o${i}`} defaultValue={one(`o${i}`) ?? "gte"}>
                <option value="gte">≥</option>
                <option value="lte">≤</option>
              </select>
            </div>
            <div>
              <label htmlFor={`v${i}`}>Value</label>
              <input id={`v${i}`} name={`v${i}`} size={6} placeholder="0.10"
                     defaultValue={one(`v${i}`) ?? ""} />
            </div>
          </div>
        ))}
        <div>
          {/* The field that is the product. */}
          <label htmlFor="as_of">As of date <span title="Uses only figures filed on or before this date">ⓘ</span></label>
          <input id="as_of" name="as_of" type="date" defaultValue={asOf} />
        </div>
        <button type="submit">Screen</button>
      </form>

      <p className="sub" style={{ marginTop: 10 }}>
        Ratios are decimals: <code>0.10</code> is 10%.{" "}
        {asOf
          ? <span className="pill hist">historical — only figures filed on or before {asOf}</span>
          : <span className="pill">live — latest known figures, restatements included</span>}
        {worst !== null && worst > 3 && (
          <span className="pill stale" style={{ marginLeft: 6 }}>data {worst} days stale</span>
        )}
      </p>

      {error && <div className="disclaimer" style={{ borderLeftColor: "var(--bad)" }}>{error}</div>}

      {criteria.length === 0 ? (
        <div className="scroll"><div className="empty">
          Pick a metric and a threshold. Then set an as-of date and run the same
          screen again — the difference is the point.
        </div></div>
      ) : (
        <>
          <h2>{rows.length} {rows.length === 1 ? "company" : "companies"} match</h2>
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>Company</th>
                  {shown.map((code) => <th key={code}>{byCode.get(code)?.label ?? code}</th>)}
                  <th>Provenance</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 && (
                  <tr><td colSpan={shown.length + 2} className="empty">
                    No company met every criterion. That is an ordinary answer, not an error.
                  </td></tr>
                )}
                {rows.map((r) => (
                  <tr key={r.instrument_id}>
                    <td>
                      <Link href={`/company/${r.instrument_id}${asOf ? `?as_of=${asOf}` : ""}`}>
                        {r.ticker ?? `#${r.instrument_id}`}
                      </Link>
                    </td>
                    {shown.map((code) => (
                      <td key={code}>
                        {r.metrics[code]
                          ? pct(r.metrics[code].value, byCode.get(code)?.unit ?? "RATIO")
                          : "—"}
                      </td>
                    ))}
                    <td style={{ textAlign: "left" }}>
                      <Provenance factIds={r.metrics[shown[0]]?.inputs ?? []} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <h2>Data freshness</h2>
      <div className="scroll">
        <table>
          <thead><tr>
            <th>Source</th><th>Kind</th><th>Last success</th><th>Last outcome</th><th>Facts written</th>
          </tr></thead>
          <tbody>
            {fresh.map((f, i) => (
              <tr key={i}>
                <td>{f.source_id}</td>
                <td>{f.kind ?? "—"}</td>
                <td>{f.last_success ? new Date(f.last_success).toISOString().slice(0, 10) : "never"}</td>
                <td>{f.last_outcome ?? "—"}</td>
                <td>{f.rows_written ?? 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="sub" style={{ marginTop: 8 }}>
        Figures are as filed with the SEC under IFRS or US-GAAP, not the Ind AS
        numbers filed in India — so they will not match Indian screeners.
        Coverage is the Indian companies that file with the SEC, which is a
        small set. Mostly annual periods, with some quarterly.
      </p>
    </>
  );
}
