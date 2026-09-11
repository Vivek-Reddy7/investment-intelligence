/**
 * API contract smoke test. Run against a live dev server:
 *
 *     npm run dev &
 *     node scripts/contract-test.mjs
 *
 * Deliberately not a unit test suite. What it checks is the things a frontend
 * would silently break on: response envelope shape, that every response
 * carries freshness, that the historical and live paths both work, and that
 * bad input is refused with 400 rather than leaking a 500 or, worse,
 * succeeding.
 *
 * The injection cases matter most. A screen is user input reaching a WHERE
 * clause, so the operator and metric must come from allow-lists. These assert
 * the refusal rather than trusting that they do.
 */

const BASE = process.env.BASE ?? "http://localhost:3100";
let failures = 0;

function check(name, condition, detail = "") {
  if (condition) {
    console.log(`  ok    ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${name} ${detail}`);
  }
}

async function get(path) {
  const response = await fetch(`${BASE}${path}`);
  let body = null;
  try { body = await response.json(); } catch { /* non-JSON is itself a failure */ }
  return { status: response.status, body };
}

console.log("\n/api/v1/meta");
{
  const { status, body } = await get("/api/v1/meta");
  check("200", status === 200, `got ${status}`);
  check("has data.metrics", Array.isArray(body?.data?.metrics));
  check("every metric has a definition",
    body?.data?.metrics?.every((m) => m.definition && m.label && m.unit));
  check("envelope carries freshness", Array.isArray(body?.meta?.freshness));
  check("envelope carries server time", typeof body?.meta?.as_of_server === "string");
}

console.log("\n/api/v1/screen — live");
{
  const { status, body } = await get(
    "/api/v1/screen?fy=2019&currency=USD&c=NET_MARGIN:gte:0.10&c=ROE:gte:0.12");
  check("200", status === 200, `got ${status}`);
  check("historical flag is false", body?.meta?.historical === false);
  check("matched count agrees with rows", body?.meta?.matched === body?.data?.length);
  const first = body?.data?.[0];
  check("row has both screened metrics",
    first && "NET_MARGIN" in first.metrics && "ROE" in first.metrics);
  check("metric carries provenance inputs",
    Array.isArray(first?.metrics?.NET_MARGIN?.inputs) &&
    first.metrics.NET_MARGIN.inputs.length > 0);
  check("inputs are numbers, not strings",
    first?.metrics?.NET_MARGIN?.inputs?.every((n) => typeof n === "number"));
}

console.log("\n/api/v1/screen — historical");
{
  const { status, body } = await get(
    "/api/v1/screen?fy=2019&currency=USD&as_of=2020-06-30&c=NET_MARGIN:gte:0.10");
  check("200", status === 200, `got ${status}`);
  check("historical flag is true", body?.meta?.historical === true);
  check("as_of echoed back", body?.meta?.as_of === "2020-06-30");
}

console.log("\n/api/v1/screen — an as-of date before anything was filed");
{
  const { status, body } = await get(
    "/api/v1/screen?fy=2019&currency=USD&as_of=2000-01-01&c=NET_MARGIN:gte:0.10");
  check("200", status === 200, `got ${status}`);
  check("returns nothing, rather than today's answer", body?.data?.length === 0);
}

console.log("\n/api/v1/explain");
{
  const screen = await get("/api/v1/screen?fy=2019&currency=USD&c=NET_MARGIN:gte:0.10");
  const ids = screen.body.data[0].metrics.NET_MARGIN.inputs;
  const { status, body } = await get(`/api/v1/explain?facts=${ids.join(",")}`);
  check("200", status === 200, `got ${status}`);
  check("one row per input fact", body?.data?.length === ids.length);
  check("every row names its filing", body?.data?.every((r) => r.source_ref?.startsWith("http")));
  check("every row names its licence", body?.data?.every((r) => r.licence_note));
  check("every row carries known_from", body?.data?.every((r) => r.known_from));
}

console.log("\n/api/v1/company/:id");
{
  const { status, body } = await get("/api/v1/company/1");
  check("200", status === 200, `got ${status}`);
  check("has identifiers", Array.isArray(body?.data?.identifiers));
  check("has years with facts", body?.data?.years?.[0]?.facts?.length > 0);
  const missing = await get("/api/v1/company/999999");
  check("unknown id is 404", missing.status === 404, `got ${missing.status}`);
  const bad = await get("/api/v1/company/not-a-number");
  check("non-numeric id is 400", bad.status === 400, `got ${bad.status}`);
}

console.log("\nbad input is refused");
{
  for (const [name, path, expected] of [
    ["unknown metric", "/api/v1/screen?fy=2019&c=NOPE:gte:1", 400],
    ["unknown operator", "/api/v1/screen?fy=2019&c=ROE:%3B%20DROP%20TABLE%20financial_facts%3B%20--:1", 400],
    ["non-numeric threshold", "/api/v1/screen?fy=2019&c=ROE:gte:1%3BDELETE", 400],
    ["malformed criterion", "/api/v1/screen?fy=2019&c=justonepart", 400],
    ["no criteria", "/api/v1/screen?fy=2019", 400],
    ["non-numeric fiscal year", "/api/v1/screen?fy=abc&c=ROE:gte:1", 400],
    ["non-numeric fact id", "/api/v1/explain?facts=1%3Bdrop", 400],
  ]) {
    const { status, body } = await get(path);
    check(`${name} -> ${expected}`, status === expected, `got ${status}`);
    check(`${name} explains itself`, typeof body?.error === "string");
  }
}

console.log("\nthe store is unharmed after all of that");
{
  const { body } = await get("/api/v1/meta");
  check("metrics still present", body?.data?.metrics?.length === 9,
    `got ${body?.data?.metrics?.length}`);
}

console.log(failures === 0 ? "\nall contract checks passed\n" : `\n${failures} FAILED\n`);
process.exit(failures === 0 ? 0 : 1);
