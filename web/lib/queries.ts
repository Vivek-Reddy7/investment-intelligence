/**
 * The service layer. All SQL lives here.
 *
 * Architecture §2.8 requires route handlers to be thin over a service layer
 * that does not know it is being called over HTTP, so extracting the API into
 * its own process later is mechanical rather than a rewrite. Nothing in this
 * file imports from `next`.
 *
 * Two rules this file exists to keep:
 *
 *  - **Invariant 9: no serving path triggers an external fetch.** Every query
 *    reads our own store. There is no HTTP client in the web app at all.
 *  - **Invariant 12: every query is as-of dated.** `asOf` is a required
 *    argument on the fact-reading functions, not an optional extra. A caller
 *    that wants "now" has to say so.
 */

import { Pool } from "pg";

// One pool per process. Neon's free tier suspends after 5 minutes idle and
// resumes on connection (ADR 002), so the first request after a quiet period
// pays a cold start. That is latency, not failure, and the pool handles it.
const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  max: 3,
  idleTimeoutMillis: 30_000,
  connectionTimeoutMillis: 15_000,
});

export type Freshness = {
  source_id: string;
  kind: string;
  last_success: string | null;
  last_attempt: string | null;
  last_outcome: string | null;
  rows_written: number | null;
  stale_days: number | null;
};

export type MetricDef = {
  metric_code: string;
  label: string;
  definition: string;
  family: string;
  unit: string;
  higher_is_better: boolean | null;
};

export type Criterion = { metric: string; op: string; value: string };

// Allow-list. A screen is user input reaching a WHERE clause, so the operator
// never comes from the request unchecked. Mirrors analytics/screen.py.
const OPERATORS: Record<string, string> = {
  gte: ">=", gt: ">", lte: "<=", lt: "<", eq: "=", ne: "<>",
};

export class BadRequest extends Error {}

/**
 * Data freshness, from the ingestion run log.
 *
 * The UI shows this because the honest answer is sometimes "four days stale,
 * ingestion is failing" — and a platform that hides that is worse than one
 * with no data at all. Architecture §2.5.
 */
export async function freshness(): Promise<Freshness[]> {
  const { rows } = await pool.query<Freshness>(`
    SELECT s.source_id,
           r.kind,
           max(r.finished_at) FILTER (WHERE r.outcome = 'SUCCESS') AS last_success,
           max(r.started_at)                                       AS last_attempt,
           (array_agg(r.outcome ORDER BY r.started_at DESC))[1]    AS last_outcome,
           sum(r.rows_written)                                     AS rows_written,
           extract(day FROM now() - max(r.finished_at)
                   FILTER (WHERE r.outcome = 'SUCCESS'))::int      AS stale_days
    FROM   sources s
    LEFT   JOIN ingestion_runs r USING (source_id)
    GROUP  BY s.source_id, r.kind
    ORDER  BY s.source_id
  `);
  return rows;
}

export type JobHealth = {
  source_id: string; kind: string; max_age: string;
  last_success: string | null; age: string | null;
  last_outcome: string | null; status: string;
};

/**
 * Expected-vs-actual ingestion, including jobs that did not run at all.
 *
 * Distinct from `freshness()`, which summarises runs that happened. This one
 * reports on an EXPECTATION, which is the only way an absent run is visible.
 * See migration 015.
 */
export async function ingestionHealth(): Promise<JobHealth[]> {
  const { rows } = await pool.query<JobHealth>(
    `SELECT source_id, kind, max_age::text, last_success, age::text,
            last_outcome, status
     FROM   ingestion_health()`);
  return rows;
}

export type StatusSnapshot = {
  overall: string;
  health: JobHealth[];
  open_alerts: Record<string, unknown>[];
  inert_checks: string[];
  recent_runs: Record<string, unknown>[];
  rejections: Record<string, unknown>[];
  coverage: Record<string, unknown>;
};

/**
 * The operational snapshot, for /status.
 *
 * Read-only and deliberately public. Everything here is already inferable
 * from the site — the freshness banner says when data last updated, and every
 * figure links to its filing — so publishing the operational view costs
 * nothing and makes staleness impossible to miss. It exposes no connection
 * string, no user data and no secret; `detail` fields describe ingestion
 * state, not infrastructure.
 *
 * Detection is NOT run here. A page render must not have side effects, and a
 * crawler hitting /status should not be able to open alerts. The scheduled
 * job calls `cli status` for that.
 */
export async function statusSnapshot(): Promise<StatusSnapshot> {
  const [health, alerts, runs, quality, rejections, coverage] = await Promise.all([
    pool.query(`SELECT source_id, kind, max_age::text, last_success, age::text,
                       last_outcome, status FROM ingestion_health()`),
    pool.query(`SELECT alert_id, source_id, kind, status, severity, detail,
                       fired_at, notified_at
                FROM app.operational_alerts WHERE resolved_at IS NULL
                ORDER BY severity, fired_at`),
    pool.query(`SELECT run_id, source_id, kind, outcome, started_at,
                       rows_written, rows_rejected, error,
                       round(extract(epoch FROM finished_at - started_at)::numeric, 1)
                         AS seconds
                FROM ingestion_runs ORDER BY started_at DESC LIMIT 10`),
    pool.query(`SELECT check_code, severity, evaluable, findings
                FROM quality_coverage()`),
    pool.query(`SELECT reason_class, count(*)::int AS rejections,
                       count(DISTINCT instrument_ref)::int AS instruments
                FROM ingestion_rejections GROUP BY reason_class
                ORDER BY count(*) DESC`),
    pool.query(`SELECT count(DISTINCT instrument_id)::int AS instruments,
                       count(*)::int AS facts,
                       count(DISTINCT period_type)::int AS period_types,
                       -- ::text on purpose. A DATE has no timezone, and pg
                       -- hands it back as a JS Date, which then renders
                       -- through the viewer's offset -- enough to display the
                       -- wrong day for a date near midnight. Casting in SQL
                       -- avoids the conversion rather than correcting it.
                       min(period_end)::text AS earliest,
                       max(period_end)::text AS latest
                FROM financial_facts`),
  ]);

  const inert = quality.rows.filter((q) => q.evaluable === 0).map((q) => q.check_code);
  const unhealthy = health.rows.filter((h) => h.status !== "OK");
  const overall =
    health.rowCount === 0 ? "UNMONITORED"
    : alerts.rows.some((a) => a.severity === "CRITICAL") ? "CRITICAL"
    : unhealthy.length || inert.length ? "DEGRADED"
    : "OK";

  return {
    overall,
    health: health.rows,
    open_alerts: alerts.rows,
    inert_checks: inert,
    recent_runs: runs.rows,
    rejections: rejections.rows,
    coverage: coverage.rows[0] ?? {},
  };
}

export async function metricDefinitions(): Promise<MetricDef[]> {
  const { rows } = await pool.query<MetricDef>(`
    SELECT metric_code, label, definition, family, unit, higher_is_better
    FROM   metrics ORDER BY display_order
  `);
  return rows;
}

export async function fiscalYears(): Promise<number[]> {
  const { rows } = await pool.query<{ fiscal_year: number }>(`
    SELECT DISTINCT fiscal_year FROM metric_values ORDER BY fiscal_year DESC
  `);
  return rows.map((r) => r.fiscal_year);
}

export async function currencies(): Promise<string[]> {
  const { rows } = await pool.query<{ currency: string }>(`
    SELECT DISTINCT currency FROM metric_values ORDER BY currency
  `);
  return rows.map((r) => r.currency.trim());
}

export type ScreenRow = {
  instrument_id: number;
  ticker: string | null;
  fiscal_year: number;
  currency: string;
  metrics: Record<string, { value: string; inputs: number[] }>;
};

/**
 * Screening, live or as of a past date.
 *
 * `asOf` and `on` both matter and are both needed for a truthful historical
 * screen: `asOf` restricts the facts to those known by then, `on` restricts
 * the universe to companies reporting by then. Only the first leaks
 * survivorship; only the second leaks lookahead.
 *
 * The live path reads the materialised `metric_values`; the historical path
 * computes from `metrics_as_of()`. Both use the same metric definitions,
 * because the materialised table IS that function called with now() —
 * see migration 013.
 */
export async function screen(opts: {
  criteria: Criterion[];
  fiscalYear: number;
  currency: string;
  asOf?: string | null;
  basis?: string;
  periodType?: string;
  limit?: number;
}): Promise<ScreenRow[]> {
  const {
    criteria, fiscalYear, currency, asOf = null,
    basis = "CONSOLIDATED", periodType = "ANNUAL", limit = 200,
  } = opts;

  if (criteria.length === 0) throw new BadRequest("at least one criterion is required");
  if (criteria.length > 8) throw new BadRequest("at most 8 criteria");

  const known = new Set((await metricDefinitions()).map((m) => m.metric_code));
  for (const c of criteria) {
    if (!known.has(c.metric)) throw new BadRequest(`unknown metric: ${c.metric}`);
    if (!(c.op in OPERATORS)) throw new BadRequest(`unknown operator: ${c.op}`);
    if (!/^-?\d+(\.\d+)?$/.test(c.value)) throw new BadRequest(`threshold must be numeric: ${c.value}`);
  }

  const historical = asOf !== null;
  const source = historical ? "metrics_as_of($1::timestamptz)" : "metric_values";
  // COALESCE so $1 is referenced (and therefore typeable) on BOTH paths.
  // Without it the live query never mentions $1 and Postgres reports
  // "could not determine data type of parameter $1" -- which surfaced as a
  // 500 on the live screen while the historical one worked, because the
  // historical SQL does reference it.
  const universeAsOf = "COALESCE($1::timestamptz, now())";
  const on = "COALESCE($1::timestamptz::date, current_date)";

  const params: unknown[] = [asOf, basis, fiscalYear, periodType, currency, limit];
  // Each criterion is its own EXISTS. One pivoted row per company would let a
  // company missing a screened metric pass on a NULL; this makes it fail.
  const conditions = criteria.map((c, i) => {
    params.push(c.metric, c.value);
    const codeIdx = params.length - 1;
    const valIdx = params.length;
    return `EXISTS (
      SELECT 1 FROM ${source} c${i}
      WHERE c${i}.instrument_id = u.instrument_id
        AND c${i}.basis = $2 AND c${i}.fiscal_year = $3
        AND c${i}.period_type = $4 AND c${i}.currency = $5
        AND c${i}.metric_code = $${codeIdx}
        AND c${i}.value ${OPERATORS[c.op]} $${valIdx}::numeric
    )`;
  });

  const { rows } = await pool.query(`
    WITH u AS (SELECT instrument_id FROM universe_as_of(${on}, ${universeAsOf}))
    SELECT u.instrument_id,
           (SELECT value FROM instrument_external_ids
             WHERE instrument_id = u.instrument_id AND scheme = 'US_TICKER') AS ticker,
           m.fiscal_year, m.currency, m.metric_code, m.value::text AS value, m.inputs
    FROM   u
    JOIN   ${source} m ON m.instrument_id = u.instrument_id
    WHERE  ${conditions.join(" AND ")}
      AND  m.basis = $2 AND m.fiscal_year = $3
      AND  m.period_type = $4 AND m.currency = $5
    ORDER  BY u.instrument_id, m.metric_code
    LIMIT  $6
  `, params);

  const byInstrument = new Map<number, ScreenRow>();
  for (const r of rows) {
    let row = byInstrument.get(r.instrument_id);
    if (!row) {
      row = {
        instrument_id: r.instrument_id,
        ticker: r.ticker,
        fiscal_year: r.fiscal_year,
        currency: r.currency.trim(),
        metrics: {},
      };
      byInstrument.set(r.instrument_id, row);
    }
    // pg returns bigint[] as strings, because a bigint can exceed Number's
    // safe integer range. Fact ids will not, so converting here keeps the
    // declared type honest rather than quietly lying about string[].
    row.metrics[r.metric_code] = {
      value: r.value,
      inputs: (r.inputs as unknown[]).map(Number),
    };
  }
  return [...byInstrument.values()];
}

export type CompanyDetail = {
  instrument_id: number;
  ticker: string | null;
  identifiers: { scheme: string; value: string }[];
  years: {
    fiscal_year: number;
    currency: string;
    facts: { line_item: string; value: string; known_from: string; filing_id: number }[];
    metrics: { metric_code: string; value: string; inputs: number[] }[];
  }[];
};

export async function company(instrumentId: number, asOf: string | null): Promise<CompanyDetail | null> {
  const ids = await pool.query<{ scheme: string; value: string }>(
    `SELECT scheme, value FROM instrument_external_ids WHERE instrument_id = $1 ORDER BY scheme`,
    [instrumentId]);
  if (ids.rowCount === 0) return null;

  const stamp = asOf ?? new Date().toISOString();

  const facts = await pool.query(`
    SELECT fiscal_year, currency, line_item, value::text AS value,
           known_from, filing_id
    FROM   facts_as_of($2::timestamptz)
    WHERE  instrument_id = $1 AND period_type = 'ANNUAL'
    ORDER  BY fiscal_year DESC, line_item
  `, [instrumentId, stamp]);

  const metrics = await pool.query(`
    SELECT fiscal_year, currency, metric_code, value::text AS value, inputs
    FROM   metrics_as_of($2::timestamptz)
    WHERE  instrument_id = $1 AND period_type = 'ANNUAL'
    ORDER  BY fiscal_year DESC, metric_code
  `, [instrumentId, stamp]);

  const key = (fy: number, cur: string) => `${fy}|${cur.trim()}`;
  const years = new Map<string, CompanyDetail["years"][number]>();
  const ensure = (fy: number, cur: string) => {
    const k = key(fy, cur);
    if (!years.has(k)) years.set(k, { fiscal_year: fy, currency: cur.trim(), facts: [], metrics: [] });
    return years.get(k)!;
  };
  for (const f of facts.rows) {
    ensure(f.fiscal_year, f.currency).facts.push({
      line_item: f.line_item, value: f.value,
      known_from: f.known_from, filing_id: f.filing_id,
    });
  }
  for (const m of metrics.rows) {
    ensure(m.fiscal_year, m.currency).metrics.push({
      metric_code: m.metric_code, value: m.value,
      inputs: (m.inputs as unknown[]).map(Number),
    });
  }

  return {
    instrument_id: instrumentId,
    ticker: ids.rows.find((r) => r.scheme === "US_TICKER")?.value ?? null,
    identifiers: ids.rows,
    years: [...years.values()].sort(
      (a, b) => b.fiscal_year - a.fiscal_year || a.currency.localeCompare(b.currency)),
  };
}

/**
 * Provenance. Resolves a metric's input fact ids to the figures and the
 * documents behind them.
 *
 * This is the Phase 1 promise made clickable: show the number, and show how it
 * was derived. A metric that cannot answer this is a defect, not a gap in the
 * UI.
 */
export async function explain(factIds: number[]) {
  if (factIds.length === 0) throw new BadRequest("no fact ids given");
  if (factIds.length > 50) throw new BadRequest("too many fact ids");
  const { rows } = await pool.query(`
    SELECT f.fact_id, f.line_item, f.value::text AS value, f.currency,
           f.fiscal_year, f.period_type, f.period_start, f.period_end,
           f.known_from, fl.source_ref, fl.filed_at, fl.filing_type,
           s.source_id, s.licence_note, s.licence_url
    FROM   financial_facts f
    JOIN   filings fl ON fl.filing_id = f.filing_id
    JOIN   sources s  ON s.source_id  = f.source_id
    WHERE  f.fact_id = ANY($1::bigint[])
    ORDER  BY f.line_item
  `, [factIds]);
  return rows;
}
