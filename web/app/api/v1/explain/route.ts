import { explain, BadRequest } from "@/lib/queries";
import { fail, ok } from "@/lib/http";

export const dynamic = "force-dynamic";

/** GET /api/v1/explain?facts=12,13 — the provenance chain for a metric. */
export async function GET(request: Request) {
  try {
    const raw = new URL(request.url).searchParams.get("facts") ?? "";
    const ids = raw.split(",").filter(Boolean).map((s) => {
      const n = Number(s);
      if (!Number.isInteger(n)) throw new BadRequest(`not a fact id: ${s}`);
      return n;
    });
    return ok(await explain(ids));
  } catch (err) { return fail(err); }
}
