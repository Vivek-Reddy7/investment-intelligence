import { company, BadRequest } from "@/lib/queries";
import { fail, ok } from "@/lib/http";
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    const instrumentId = Number(id);
    if (!Number.isInteger(instrumentId)) throw new BadRequest("id must be an integer");
    const asOf = new URL(request.url).searchParams.get("as_of");
    const detail = await company(instrumentId, asOf || null);
    if (!detail) return NextResponse.json({ error: "not found" }, { status: 404 });
    return ok(detail, { as_of: asOf || null });
  } catch (err) { return fail(err); }
}
