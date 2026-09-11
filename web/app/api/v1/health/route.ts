import { NextResponse } from "next/server";
import { ingestionHealth } from "@/lib/queries";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/health — for an external uptime monitor.
 *
 * This endpoint exists because of a circularity: the ingestion health check
 * runs inside the same GitHub Actions workflow as the ingestion, so it cannot
 * detect the workflow itself being disabled (see .github/workflows/ingest.yml).
 * Detection has to live somewhere the scheduler does not.
 *
 * Returns **503** when any expected job is stale or has never run, so a free
 * uptime monitor pointed here alerts without needing to parse the body.
 * Returning 200 with a sad payload would be technically informative and
 * practically silent.
 *
 * "Nothing is monitored" is also 503, deliberately. A system with no declared
 * expectations cannot be healthy; it can only be unobserved.
 */
export async function GET() {
  try {
    const rows = await ingestionHealth();
    const bad = rows.filter((r) => r.status !== "OK");
    const nothingMonitored = rows.length === 0;
    return NextResponse.json(
      {
        status: nothingMonitored ? "UNMONITORED" : bad.length ? "DEGRADED" : "OK",
        jobs: rows,
      },
      { status: nothingMonitored || bad.length ? 503 : 200 },
    );
  } catch (err) {
    console.error("health check failed", err);
    return NextResponse.json({ status: "ERROR" }, { status: 503 });
  }
}
