import { currencies, fiscalYears, metricDefinitions } from "@/lib/queries";
import { fail, ok } from "@/lib/http";

export const dynamic = "force-dynamic";

/** Everything the UI needs to build a screen form. */
export async function GET() {
  try {
    const [metrics, years, curr] = await Promise.all([
      metricDefinitions(), fiscalYears(), currencies(),
    ]);
    return ok({ metrics, fiscal_years: years, currencies: curr });
  } catch (err) { return fail(err); }
}
