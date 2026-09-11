/**
 * The operational status page.
 *
 * Distinct from the freshness banner on the screener, which answers "is what
 * I am reading current?" for a visitor in one line. This answers "is the
 * system working?" and shows the failure's shape.
 *
 * Public, deliberately. Everything here is already inferable from the site,
 * and publishing it makes staleness impossible to miss rather than something
 * only we can see. No connection strings, no user data, no secrets.
 *
 * Renders without side effects: detection runs in the scheduled job, so a
 * crawler cannot open alerts by loading this page.
 */
import { statusSnapshot } from "@/lib/queries";

export const dynamic = "force-dynamic";

const TONE: Record<string, string> = {
  OK: "var(--accent)",
  DEGRADED: "var(--warn)",
  CRITICAL: "var(--bad)",
  UNMONITORED: "var(--bad)",
};

export default async function StatusPage() {
  const s = await statusSnapshot();
  const when = (v: unknown) =>
    v ? new Date(String(v)).toISOString().replace("T", " ").slice(0, 19) : "never";

  return (
    <>
      <h1 style={{ fontSize: 22 }}>
        System status{" "}
        <span className="pill" style={{ borderColor: TONE[s.overall], color: TONE[s.overall] }}>
          {s.overall}
        </span>
      </h1>
      <p className="sub">
        {s.overall === "UNMONITORED"
          ? "Nothing is scheduled, so nothing is being observed. That is not the same as working."
          : s.overall === "OK"
          ? "Ingestion is current and every quality check evaluated something."
          : "Something needs attention. Detail below."}
      </p>

      {s.open_alerts.length > 0 && (
        <>
          <h2>Open alerts</h2>
          <div className="scroll">
            <table>
              <thead><tr>
                <th>Severity</th><th>Job</th><th>Status</th><th>Since</th><th>Notified</th>
              </tr></thead>
              <tbody>
                {s.open_alerts.map((a: any) => (
                  <tr key={String(a.alert_id)}>
                    <td style={{ textAlign: "left", color: TONE[a.severity === "CRITICAL" ? "CRITICAL" : "DEGRADED"] }}>
                      {a.severity}
                    </td>
                    <td style={{ textAlign: "left" }}>{a.source_id}/{a.kind}</td>
                    <td>{a.status}</td>
                    <td>{when(a.fired_at)}</td>
                    <td>{a.notified_at ? "yes" : "no"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {s.open_alerts.map((a: any) => (
            <p className="sub" key={`d-${a.alert_id}`}>{a.detail}</p>
          ))}
        </>
      )}

      <h2>Ingestion</h2>
      <div className="scroll">
        <table>
          <thead><tr>
            <th>Source</th><th>Kind</th><th>Status</th><th>Last success</th><th>Limit</th>
          </tr></thead>
          <tbody>
            {s.health.length === 0 && (
              <tr><td colSpan={5} className="empty">No job is scheduled.</td></tr>
            )}
            {s.health.map((h, i) => (
              <tr key={i}>
                <td style={{ textAlign: "left" }}>{h.source_id}</td>
                <td>{h.kind}</td>
                <td style={{ color: h.status === "OK" ? undefined : TONE.CRITICAL }}>
                  {h.status}
                </td>
                <td>{when(h.last_success)}</td>
                <td>{h.max_age}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Recent runs</h2>
      <div className="scroll">
        <table>
          <thead><tr>
            <th>Run</th><th>Outcome</th><th>Seconds</th><th>Written</th><th>Rejected</th><th>Started</th>
          </tr></thead>
          <tbody>
            {s.recent_runs.map((r: any) => (
              <tr key={String(r.run_id)}>
                <td style={{ textAlign: "left" }}>{r.run_id}</td>
                <td style={{ color: r.outcome === "SUCCESS" ? undefined : TONE.DEGRADED }}>
                  {r.outcome}
                </td>
                <td>{r.seconds ?? "—"}</td>
                <td>{r.rows_written}</td>
                <td>{r.rows_rejected}</td>
                <td>{when(r.started_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {s.inert_checks.length > 0 && (
        <>
          <h2>Inert quality checks</h2>
          <p className="sub">
            These evaluated zero periods. A check that never ran is not a
            passing check, so they degrade the verdict above rather than
            reading as clean: {s.inert_checks.join(", ")}.
          </p>
        </>
      )}

      <h2>Coverage</h2>
      <p className="sub">
        {String(s.coverage.instruments ?? 0)} instruments ·{" "}
        {String(s.coverage.facts ?? 0)} facts ·{" "}
        {String(s.coverage.period_types ?? 0)} period types ·{" "}
        {String(s.coverage.earliest ?? "—")} to {String(s.coverage.latest ?? "—")}
      </p>

      {s.rejections.length > 0 && (
        <>
          <h2>Rejected at ingestion</h2>
          <div className="scroll">
            <table>
              <thead><tr><th>Reason</th><th>Rejections</th><th>Instruments</th></tr></thead>
              <tbody>
                {s.rejections.map((r: any) => (
                  <tr key={r.reason_class}>
                    <td style={{ textAlign: "left" }}>{r.reason_class}</td>
                    <td>{r.rejections}</td>
                    <td>{r.instruments}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="sub">
            <code>YTD_NOT_STORED</code> is a decision, not a failure:
            year-to-date periods overlap the quarters they contain and would
            double-count in any aggregate.
          </p>
        </>
      )}
    </>
  );
}
