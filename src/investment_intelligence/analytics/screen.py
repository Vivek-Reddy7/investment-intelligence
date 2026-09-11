"""Screening: multi-criteria filters over the universe, as of any date.

The whole product in one module. Two paths, deliberately:

- **Live** screens read the materialised `metric_values`, which is fast.
- **Historical** screens compute from `metrics_as_of(as_of)` on demand, which
  is slower and correct.

Architecture §2.12 accepted that trade: someone asking what a screen would
have returned in 2019 will tolerate a couple of seconds; someone filtering
today's universe will not.

Both paths go through the SAME metric definitions, because `metric_values` is
populated by `metrics_as_of(now())`. If they had separate definitions they
would drift, and the drift would present as a screen giving different answers
depending on the date asked about -- which looks exactly like the feature
working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

import psycopg

# Comparators allowed in a criterion. An allow-list rather than string
# interpolation: a screen is user input reaching a WHERE clause, so the
# operator cannot come from the user unchecked.
OPERATORS = {
    "gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "=", "ne": "<>",
}


class InvalidCriterion(ValueError):
    """A screen we will not run."""


@dataclass(frozen=True)
class Criterion:
    metric_code: str
    operator: str
    threshold: Decimal

    def __post_init__(self) -> None:
        if self.operator not in OPERATORS:
            raise InvalidCriterion(
                f"operator must be one of {sorted(OPERATORS)}, got {self.operator!r}"
            )
        if not isinstance(self.threshold, Decimal):
            raise InvalidCriterion("threshold must be Decimal, not float")


@dataclass(frozen=True)
class ScreenResult:
    instrument_id: int
    fiscal_year: int
    currency: str
    values: dict[str, Decimal]
    inputs: dict[str, list[int]] = field(default_factory=dict)


def _validate(conn: psycopg.Connection, criteria: list[Criterion]) -> None:
    """Reject unknown metric codes before building SQL.

    Metric codes are compared against the `metrics` table rather than a Python
    list, so a metric added in a migration is screenable immediately and a
    typo fails loudly instead of returning an empty screen -- which would look
    like "no companies match".
    """
    if not criteria:
        raise InvalidCriterion("a screen needs at least one criterion")
    with conn.cursor() as cur:
        cur.execute("SELECT metric_code FROM metrics")
        known = {row[0] for row in cur.fetchall()}
    unknown = {c.metric_code for c in criteria} - known
    if unknown:
        raise InvalidCriterion(f"unknown metric(s): {sorted(unknown)}")


def screen(
    conn: psycopg.Connection,
    criteria: list[Criterion],
    *,
    fiscal_year: int,
    currency: str = "INR",
    basis: str = "CONSOLIDATED",
    period_type: str = "ANNUAL",
    as_of: datetime | None = None,
    on: date | None = None,
    limit: int = 200,
) -> list[ScreenResult]:
    """Companies meeting every criterion.

    `as_of` and `on` together make a historical screen:

      * `as_of` filters the FACTS to those we had learned by then, so a figure
        restated afterwards is not used.
      * `on` filters the UNIVERSE to companies listed and reporting by then, so
        a company that had not yet listed cannot appear and one that later
        delisted still can.

    Both are needed. Using only `as_of` would screen today's universe with
    then-known figures, which still leaks survivorship. Using only `on` would
    screen the right companies with restated figures, which leaks lookahead.
    """
    _validate(conn, criteria)

    historical = as_of is not None
    universe_on = on or (as_of.date() if as_of else date.today())

    # One definition, two access paths -- see the module docstring.
    metric_source = "metrics_as_of(%(as_of)s)" if historical else "metric_values"

    # Every criterion becomes its own EXISTS. A single pivoted row per
    # instrument would be tidier SQL and would silently pass a company that is
    # missing one of the metrics; EXISTS per criterion means a company must
    # satisfy each one explicitly, and a company with no ROE simply fails an
    # ROE screen instead of slipping through on a NULL.
    conditions = []
    params: dict[str, object] = {
        "as_of": as_of,
        "universe_on": universe_on,
        "basis": basis,
        "fiscal_year": fiscal_year,
        "period_type": period_type,
        "currency": currency,
        "limit": limit,
    }
    for i, criterion in enumerate(criteria):
        params[f"code_{i}"] = criterion.metric_code
        params[f"val_{i}"] = criterion.threshold
        conditions.append(
            f"""EXISTS (
                    SELECT 1 FROM {metric_source} c{i}
                    WHERE  c{i}.instrument_id = u.instrument_id
                      AND  c{i}.basis       = %(basis)s
                      AND  c{i}.fiscal_year = %(fiscal_year)s
                      AND  c{i}.period_type = %(period_type)s
                      AND  c{i}.currency    = %(currency)s
                      AND  c{i}.metric_code = %(code_{i})s
                      AND  c{i}.value {OPERATORS[criterion.operator]} %(val_{i})s
                )"""
        )

    sql = f"""
        WITH u AS (
            SELECT instrument_id FROM universe_as_of(%(universe_on)s, {'%(as_of)s' if historical else 'now()'})
        )
        SELECT u.instrument_id,
               m.fiscal_year, m.currency, m.metric_code, m.value, m.inputs
        FROM   u
        JOIN   {metric_source} m ON m.instrument_id = u.instrument_id
        WHERE  {' AND '.join(conditions)}
          AND  m.basis       = %(basis)s
          AND  m.fiscal_year = %(fiscal_year)s
          AND  m.period_type = %(period_type)s
          AND  m.currency    = %(currency)s
        ORDER  BY u.instrument_id, m.metric_code
        LIMIT  %(limit)s
    """

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    by_instrument: dict[int, ScreenResult] = {}
    for instrument_id, fy, cur_code, metric_code, value, inputs in rows:
        result = by_instrument.get(instrument_id)
        if result is None:
            result = ScreenResult(instrument_id, fy, cur_code, {}, {})
            by_instrument[instrument_id] = result
        result.values[metric_code] = value
        result.inputs[metric_code] = list(inputs)
    return list(by_instrument.values())


def explain(conn: psycopg.Connection, metric_value_inputs: list[int]) -> list[dict]:
    """Resolve a metric's input fact_ids to the figures and documents behind them.

    This is the Phase 1 promise made operational: a reader clicks a ratio and
    gets the line items it came from, the period they cover, when each was
    reported, and the filing to read. A metric that cannot answer this is a
    defect.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT f.fact_id, f.line_item, f.value, f.currency,
                   f.fiscal_year, f.period_type, f.period_start, f.period_end,
                   f.known_from, fl.source_ref, fl.filed_at, s.source_id,
                   s.licence_note
            FROM   financial_facts f
            JOIN   filings fl ON fl.filing_id = f.filing_id
            JOIN   sources s  ON s.source_id  = f.source_id
            WHERE  f.fact_id = ANY(%s)
            ORDER  BY f.line_item
            """,
            (metric_value_inputs,),
        )
        columns = [d.name for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def refresh(conn: psycopg.Connection) -> int:
    """Recompute the materialised current view. Run after ingestion."""
    with conn.cursor() as cur:
        cur.execute("SELECT refresh_metric_values()")
        return cur.fetchone()[0]
