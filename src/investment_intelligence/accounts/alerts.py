"""Alert evaluation.

On conditions the USER defines. Not signals -- under SEBI's Research Analyst
regulations the distinction is whether the platform or the reader supplies the
judgement, and here the reader does. The platform reports that a threshold
someone chose has been crossed.

The one interesting problem is **edge triggering**. "ROE is below 15%" is true
every day once it becomes true, so an evaluator that delivers whenever the
condition holds sends the same message daily until the user turns off all
their alerts. What a reader wants is the transition: tell me when it *becomes*
true.

That needs remembered state, which is `app.alert_state`. The rule is:

    deliver when (condition is true) AND NOT (it was true last time)

and update the remembered state either way. A rule that flips back to false
re-arms, so a metric oscillating around a threshold does produce repeated
alerts -- correctly, because it genuinely keeps crossing.

Runs after ingestion and metric refresh, never during: a slow or failing
notification channel must not be able to stall ingestion (architecture §2.10).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import psycopg

log = logging.getLogger(__name__)

# Mirrors analytics/screen.py. An allow-list, because the operator reaches a
# comparison and must never come from stored user input unchecked -- a rule row
# is user input that has been sitting in the database for a month.
OPERATORS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


@dataclass
class AlertReport:
    rules_evaluated: int = 0
    conditions_met: int = 0
    delivered: int = 0
    re_armed: int = 0
    skipped_rules: list[str] = field(default_factory=list)


def _fire_sql(operator: str) -> str:
    """Build the evaluation query for one operator.

    Interpolating the comparison operator is unavoidable -- SQL has no
    parameter position for it -- which is exactly why `OPERATORS` is a
    dictionary lookup and not a string from the row. A rule whose operator is
    not a key never reaches here.
    """
    comparison = OPERATORS[operator]
    return f"""
        WITH candidate AS (
            SELECT mv.instrument_id, mv.value
            FROM   metric_values mv
            WHERE  mv.metric_code = %(metric_code)s
              AND  mv.currency    = %(currency)s
              AND  (%(instrument_id)s::bigint IS NULL
                    OR mv.instrument_id = %(instrument_id)s::bigint)
              AND  (%(fiscal_year)s::smallint IS NULL
                    OR mv.fiscal_year = %(fiscal_year)s::smallint)
              -- Only companies actually in the universe today. An alert on a
              -- delisted company is noise.
              AND  EXISTS (SELECT 1 FROM universe_as_of(current_date, now()) u
                           WHERE u.instrument_id = mv.instrument_id)
        )
        SELECT c.instrument_id,
               c.value,
               c.value {comparison} %(threshold)s::numeric AS condition_met,
               coalesce(s.was_triggered, false)            AS was_triggered
        FROM   candidate c
        LEFT   JOIN app.alert_state s
                 ON s.rule_id = %(rule_id)s AND s.instrument_id = c.instrument_id
    """


def evaluate(conn: psycopg.Connection) -> AlertReport:
    """Evaluate every enabled rule and deliver on transitions only."""
    report = AlertReport()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT rule_id, user_id, instrument_id, metric_code, operator,
                   threshold, fiscal_year, currency
            FROM   app.alert_rules
            WHERE  enabled
            ORDER  BY created_at
            """
        )
        rules = cur.fetchall()

    for (rule_id, user_id, instrument_id, metric_code, operator,
         threshold, fiscal_year, currency) in rules:
        if operator not in OPERATORS:
            # Cannot happen through the API, and if it ever does the rule is
            # skipped loudly rather than silently evaluated some other way.
            log.error("alert rule %s has an unknown operator %r", rule_id, operator)
            report.skipped_rules.append(str(rule_id))
            continue

        report.rules_evaluated += 1
        params = {
            "rule_id": rule_id, "metric_code": metric_code, "currency": currency,
            "instrument_id": instrument_id, "fiscal_year": fiscal_year,
            "threshold": threshold,
        }

        with conn.cursor() as cur:
            cur.execute(_fire_sql(operator), params)
            for inst_id, value, condition_met, was_triggered in cur.fetchall():
                if condition_met:
                    report.conditions_met += 1

                if condition_met and not was_triggered:
                    cur.execute(
                        """
                        INSERT INTO app.alert_deliveries
                            (rule_id, user_id, instrument_id, observed, threshold)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (rule_id, user_id, inst_id, value, threshold),
                    )
                    report.delivered += 1
                elif was_triggered and not condition_met:
                    # Re-armed: the next crossing will deliver again.
                    report.re_armed += 1

                cur.execute(
                    """
                    INSERT INTO app.alert_state
                        (rule_id, instrument_id, was_triggered, last_value, evaluated_at)
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (rule_id, instrument_id) DO UPDATE
                       SET was_triggered = excluded.was_triggered,
                           last_value    = excluded.last_value,
                           evaluated_at  = excluded.evaluated_at
                    """,
                    (rule_id, inst_id, bool(condition_met), value),
                )

    return report


def pending_deliveries(conn: psycopg.Connection, limit: int = 100) -> list[dict]:
    """Alerts fired but not yet sent.

    Delivery is a separate step from firing on purpose. If firing also sent the
    email, a failing mail provider would either lose the alert or block the
    evaluation cycle. This way a fired alert is durable and the sender can
    retry.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.delivery_id, d.rule_id, d.user_id, d.instrument_id,
                   d.observed, d.threshold, d.fired_at,
                   r.metric_code, r.operator, u.email
            FROM   app.alert_deliveries d
            JOIN   app.alert_rules r USING (rule_id)
            JOIN   app.users u ON u.user_id = d.user_id
            WHERE  d.delivered_at IS NULL
            ORDER  BY d.fired_at
            LIMIT  %s
            """,
            (limit,),
        )
        columns = [c.name for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def mark_delivered(conn: psycopg.Connection, delivery_id: int,
                   error: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE app.alert_deliveries SET delivered_at = now(), error = %s "
            "WHERE delivery_id = %s",
            (error, delivery_id),
        )
