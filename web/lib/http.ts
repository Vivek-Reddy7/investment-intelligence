/**
 * Shared response shaping. Kept out of the route handlers so they stay thin.
 *
 * Every successful response carries `freshness`, because a number without a
 * date on it is a claim the platform cannot support. Architecture §2.8.
 */
import { NextResponse } from "next/server";
import { BadRequest, freshness } from "./queries";

export async function ok(data: unknown, extra: Record<string, unknown> = {}) {
  return NextResponse.json({
    data,
    meta: { as_of_server: new Date().toISOString(), freshness: await freshness(), ...extra },
  });
}

export function fail(err: unknown) {
  if (err instanceof BadRequest) {
    return NextResponse.json({ error: err.message }, { status: 400 });
  }
  console.error("api error", err);
  return NextResponse.json({ error: "internal error" }, { status: 500 });
}
