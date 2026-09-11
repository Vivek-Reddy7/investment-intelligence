import { screen, BadRequest } from "@/lib/queries";
import { fail, ok } from "@/lib/http";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/screen?fy=2019&currency=USD&as_of=2020-06-30
 *                    &c=NET_MARGIN:gte:0.10&c=ROE:gte:0.12
 *
 * `as_of` is what makes this a point-in-time screen. Omit it for today.
 */
export async function GET(request: Request) {
  try {
    const q = new URL(request.url).searchParams;
    const fy = Number(q.get("fy"));
    if (!Number.isInteger(fy)) throw new BadRequest("fy must be an integer fiscal year");

    const criteria = q.getAll("c").map((raw) => {
      const [metric, op, value] = raw.split(":");
      if (!metric || !op || value === undefined) {
        throw new BadRequest(`criterion must be METRIC:op:value, got ${raw}`);
      }
      return { metric, op, value };
    });

    const asOf = q.get("as_of");
    const rows = await screen({
      criteria, fiscalYear: fy,
      currency: q.get("currency") ?? "USD",
      asOf: asOf || null,
      limit: Math.min(Number(q.get("limit") ?? 200), 500),
    });
    return ok(rows, { as_of: asOf || null, historical: Boolean(asOf), matched: rows.length });
  } catch (err) { return fail(err); }
}
