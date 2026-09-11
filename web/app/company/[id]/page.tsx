/**
 * Company page: the financial history and the metrics derived from it, both
 * as of whatever date the reader asks for.
 *
 * The `known_from` column is deliberately on screen next to every figure. It
 * is the difference between "revenue was X" and "revenue was reported as X on
 * this date, by this filing".
 */
import Link from "next/link";
import { notFound } from "next/navigation";
import { company, metricDefinitions } from "@/lib/queries";
import { Provenance } from "../../provenance";

export const dynamic = "force-dynamic";

export default async function CompanyPage({
  params, searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const sp = await searchParams;
  const asOfRaw = Array.isArray(sp.as_of) ? sp.as_of[0] : sp.as_of;
  const asOf = asOfRaw ? new Date(asOfRaw + "T23:59:59Z").toISOString() : null;

  const [detail, metrics] = await Promise.all([
    company(Number(id), asOf), metricDefinitions(),
  ]);
  if (!detail) notFound();

  const byCode = new Map(metrics.map((m) => [m.metric_code, m]));
  const fmt = (v: string, unit: string) =>
    unit === "PERCENT" ? `${(Number(v) * 100).toFixed(1)}%` : Number(v).toFixed(2);

  return (
    <>
      <p className="sub"><Link href="/">← back to screener</Link></p>
      <h1 style={{ fontSize: 22 }}>{detail.ticker ?? `Instrument #${detail.instrument_id}`}</h1>
      <p className="sub">
        {detail.identifiers.map((i) => `${i.scheme} ${i.value}`).join("  ·  ")}
        {asOfRaw
          ? <> · <span className="pill hist">as of {asOfRaw}</span></>
          : <> · <span className="pill">latest known</span></>}
      </p>

      {detail.years.length === 0 && (
        <div className="scroll"><div className="empty">
          Nothing was known about this company as of that date.
        </div></div>
      )}

      {detail.years.map((y) => (
        <section key={`${y.fiscal_year}-${y.currency}`}>
          <h2>FY{y.fiscal_year} · {y.currency}</h2>
          <div className="scroll">
            <table>
              <thead><tr>
                <th>Reported figure</th><th>Value</th><th>Reported on</th>
              </tr></thead>
              <tbody>
                {y.facts.map((f, i) => (
                  <tr key={i}>
                    <td>{f.line_item}</td>
                    <td>{Number(f.value).toLocaleString()}</td>
                    <td>{new Date(f.known_from).toISOString().slice(0, 10)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {y.metrics.length > 0 && (
            <div className="scroll" style={{ marginTop: 10 }}>
              <table>
                <thead><tr>
                  <th>Metric</th><th>Value</th><th>Definition</th><th>Inputs</th>
                </tr></thead>
                <tbody>
                  {y.metrics.map((m) => (
                    <tr key={m.metric_code}>
                      <td>{byCode.get(m.metric_code)?.label ?? m.metric_code}</td>
                      <td>{fmt(m.value, byCode.get(m.metric_code)?.unit ?? "RATIO")}</td>
                      <td style={{ textAlign: "left" }} className="sub">
                        {byCode.get(m.metric_code)?.definition}
                      </td>
                      <td style={{ textAlign: "left" }}>
                        <Provenance factIds={m.inputs} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ))}
    </>
  );
}
