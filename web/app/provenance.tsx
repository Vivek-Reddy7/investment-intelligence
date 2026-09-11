/**
 * The provenance drill-down. A server component, so the fact lookup happens on
 * the server and the browser gets no database access of any kind.
 *
 * This is the Phase 1 promise made clickable: show the number, and show how it
 * was derived. Collapsed by default because most readers will not open it --
 * but it has to be there, or the promise is decoration.
 */
import { explain } from "@/lib/queries";

export async function Provenance({ factIds }: { factIds: number[] }) {
  if (factIds.length === 0) return <span className="sub">—</span>;
  const rows = await explain(factIds);
  return (
    <details className="prov">
      <summary>show inputs</summary>
      <table>
        <thead><tr><th>Line item</th><th>Value</th><th>Reported</th><th>Filing</th></tr></thead>
        <tbody>
          {rows.map((r: any) => (
            <tr key={r.fact_id}>
              <td>{r.line_item}</td>
              <td>{r.currency} {Number(r.value).toLocaleString()}</td>
              <td>{new Date(r.known_from).toISOString().slice(0, 10)}</td>
              <td style={{ textAlign: "left" }}>
                <a href={r.source_ref} target="_blank" rel="noreferrer">{r.filing_type}</a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
