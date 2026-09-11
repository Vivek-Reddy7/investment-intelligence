"""The operational snapshot: one call, everything an operator needs.

Distinct from the freshness banner on the public site, which answers "is what
I am reading current?" for a visitor. This answers "is the system working?"
for whoever is responsible for it, and the two want different things:
a visitor needs one honest sentence, an operator needs the failure's shape.

Assembled in one query set rather than left as separate endpoints, because the
question is always asked as a whole -- a stale source, a failing run and a
quality regression are usually the same incident seen from three angles.
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg

from investment_intelligence.observability import logging as structured

log = logging.getLogger(__name__)


def _rows(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        columns = [d.name for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def snapshot(conn: psycopg.Connection) -> dict[str, Any]:
    """Everything at once: health, open alerts, recent runs, metrics, quality."""
    health = _rows(conn, "SELECT * FROM ingestion_health()")
    open_alerts = _rows(conn, """
        SELECT alert_id, source_id, kind, status, severity, detail, fired_at,
               notified_at
        FROM   app.operational_alerts
        WHERE  resolved_at IS NULL
        ORDER  BY severity, fired_at
    """)
    recent_runs = _rows(conn, """
        SELECT run_id, source_id, kind, outcome, started_at, finished_at,
               rows_written, rows_rejected, error,
               round(extract(epoch FROM finished_at - started_at)::numeric, 1)
                 AS seconds
        FROM   ingestion_runs
        ORDER  BY started_at DESC
        LIMIT  10
    """)
    metrics = _rows(conn, """
        SELECT * FROM ingestion_metrics
        WHERE  day >= current_date - 14
        ORDER  BY day DESC, source_id
    """)
    quality = _rows(conn, "SELECT * FROM quality_coverage()")
    rejections = _rows(conn, """
        SELECT reason_class, count(*) AS rejections,
               count(DISTINCT instrument_ref) AS instruments
        FROM   ingestion_rejections
        GROUP  BY reason_class ORDER BY count(*) DESC
    """)
    # Facts loaded but no metrics computed is a live-screen outage that
    # nothing else detects: ingestion is healthy, quality checks pass, and
    # every screen quietly returns nothing. It is what a backfill without a
    # metric refresh leaves behind, and "no companies match your criteria" is
    # an entirely ordinary thing for a screener to say.
    derived = _rows(conn, """
        SELECT (SELECT count(*) FROM financial_facts) AS facts,
               (SELECT count(*) FROM metric_values)   AS metrics
    """)
    coverage = _rows(conn, """
        SELECT count(DISTINCT instrument_id) AS instruments,
               count(*)                      AS facts,
               count(DISTINCT period_type)   AS period_types,
               min(period_end)               AS earliest,
               max(period_end)               AS latest
        FROM   financial_facts
    """)

    # A check that never ran is not a passing check (Phase 17 §3). Surfaced in
    # the top-line verdict so a status page cannot read green while a check is
    # inert.
    inert = [c["check_code"] for c in quality if c["evaluable"] == 0]
    unhealthy = [h for h in health if h["status"] != "OK"]
    fact_count = derived[0]["facts"]
    metric_count = derived[0]["metrics"]
    metrics_stale = fact_count > 0 and metric_count == 0

    if not health:
        overall = "UNMONITORED"
    elif any(a["severity"] == "CRITICAL" for a in open_alerts) or metrics_stale:
        overall = "CRITICAL"
    elif unhealthy or inert:
        overall = "DEGRADED"
    else:
        overall = "OK"

    return {
        "overall": overall,
        "metrics_stale": metrics_stale,
        "facts": fact_count,
        "metric_values": metric_count,
        "health": health,
        "open_alerts": open_alerts,
        "inert_checks": inert,
        "recent_runs": recent_runs,
        "metrics": metrics,
        "quality": quality,
        "rejections": rejections,
        "coverage": coverage[0] if coverage else {},
    }


def detect(conn: psycopg.Connection) -> list[dict]:
    """Run detection, log every transition, return what changed.

    Logged rather than merely returned because a transition is the single most
    interesting line in the log: it is the moment the system's state changed,
    and it is what someone will search for afterwards.
    """
    transitions = _rows(conn, "SELECT * FROM app.detect_operational_alerts()")
    for t in transitions:
        emit = structured.error if t["action"] == "OPENED" else structured.info
        emit(log, f"operational alert {t['action'].lower()}",
             source_id=t["source_id"], kind=t["kind"], status=t["status"])
    return transitions


def pending_notifications(conn: psycopg.Connection) -> list[dict]:
    """Open alerts nobody has been told about.

    Same fire/send split as user alerts: detection records, a sender drains.
    A failing channel can then neither lose an alert nor stall detection.
    """
    return _rows(conn, """
        SELECT alert_id, source_id, kind, status, severity, detail, fired_at
        FROM   app.operational_alerts
        WHERE  notified_at IS NULL AND resolved_at IS NULL
        ORDER  BY severity, fired_at
    """)


def mark_notified(conn: psycopg.Connection, alert_id: int,
                  error: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE app.operational_alerts SET notified_at = now(), "
            "notify_error = %s WHERE alert_id = %s", (error, alert_id))
