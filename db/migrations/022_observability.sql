-- 022 · Observability
--
-- The failure this phase exists for is the quiet one: data stops updating and
-- the site keeps serving stale numbers confidently, with every page
-- rendering. Phase 8 built the detection (`ingestion_health`). Nothing yet
-- turns a detection into a notification, and nothing shows an operator the
-- whole picture in one place.
--
-- Two design points carried over from elsewhere in the project, because they
-- were right there and are right here.
--
-- 1. EDGE TRIGGERING, as in user alerts (mig. 020). A health check that has
--    been STALE for a week must notify once, not seven times. An operational
--    alert that fires every cycle while a condition holds trains whoever
--    receives it to filter the channel, which is strictly worse than having
--    no alerting: the next real one is filtered too.
--
-- 2. FIRING AND SENDING ARE SEPARATE, as in user alerts. `operational_alerts`
--    records that a condition was detected; a sender drains it. A failing
--    notification channel then cannot lose the alert or stall detection.
--
-- Metrics are a view rather than a table. They are pure aggregation over
-- `ingestion_runs`, so materialising them would add a staleness problem to
-- the tool whose job is detecting staleness.

CREATE TABLE app.operational_alerts (
    alert_id     bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- What degraded. Matches ingestion_health()'s shape so the two line up.
    source_id    text        NOT NULL,
    kind         text        NOT NULL,
    status       text        NOT NULL,
    detail       text        NOT NULL,
    severity     text        NOT NULL CHECK (severity IN ('WARNING', 'CRITICAL')),
    fired_at     timestamptz NOT NULL DEFAULT now(),
    -- Set when the condition clears, so an operator can see whether something
    -- is ongoing or was a blip. A resolved alert is not deleted: the history
    -- of what broke is worth keeping.
    resolved_at  timestamptz,
    notified_at  timestamptz,
    notify_error text
);

CREATE INDEX idx_op_alerts_open ON app.operational_alerts (source_id, kind)
    WHERE resolved_at IS NULL;
CREATE INDEX idx_op_alerts_unnotified ON app.operational_alerts (fired_at)
    WHERE notified_at IS NULL;

COMMENT ON TABLE app.operational_alerts IS
    'Operational, not user-owned, so deliberately NOT row-level-secured -- '
    'there is no user to scope it to. Insert-only plus resolution: the record '
    'of what broke and when is the point.';

GRANT SELECT, INSERT, UPDATE ON app.operational_alerts TO ii_app, ii_alerts;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app TO ii_app, ii_alerts;

-- ---------------------------------------------------------------------------
-- Detection: health status -> alert, edge-triggered
-- ---------------------------------------------------------------------------
CREATE FUNCTION app.detect_operational_alerts()
RETURNS TABLE (action text, source_id text, kind text, status text)
LANGUAGE plpgsql AS $$
BEGIN
    -- Open an alert for anything unhealthy that has no open alert already.
    -- The WHERE NOT EXISTS is the edge trigger.
    RETURN QUERY
    WITH unhealthy AS (
        SELECT h.source_id, h.kind, h.status,
               CASE h.status
                   WHEN 'NEVER_RAN'       THEN 'CRITICAL'
                   WHEN 'NEVER_SUCCEEDED' THEN 'CRITICAL'
                   WHEN 'STALE'           THEN 'CRITICAL'
                   ELSE 'WARNING'
               END AS severity,
               CASE h.status
                   WHEN 'NEVER_RAN' THEN
                       'No run has ever happened. Look at the scheduler: a '
                       'disabled workflow, a wrong cron, or a missing secret.'
                   WHEN 'NEVER_SUCCEEDED' THEN
                       'Runs are happening and none has succeeded. Look at '
                       'the source, not the scheduler.'
                   WHEN 'STALE' THEN
                       'Last success was ' || coalesce(h.age::text, 'never') ||
                       ' ago, past the ' || h.max_age::text || ' limit. The '
                       'site is serving stale numbers.'
                   ELSE
                       'Last run finished ' || h.status || '.'
               END AS detail
        FROM   ingestion_health() h
        WHERE  h.status <> 'OK'
    ),
    opened AS (
        INSERT INTO app.operational_alerts
            (source_id, kind, status, detail, severity)
        SELECT u.source_id, u.kind, u.status, u.detail, u.severity
        FROM   unhealthy u
        WHERE  NOT EXISTS (
                   SELECT 1 FROM app.operational_alerts a
                   WHERE  a.source_id = u.source_id
                     AND  a.kind      = u.kind
                     AND  a.status    = u.status
                     AND  a.resolved_at IS NULL
               )
        RETURNING source_id, kind, status
    )
    SELECT 'OPENED'::text, o.source_id, o.kind, o.status FROM opened o;

    -- Resolve anything whose condition no longer holds. Without this the
    -- status page shows a permanent red light for something already fixed,
    -- which is how a dashboard stops being read.
    RETURN QUERY
    WITH resolved AS (
        UPDATE app.operational_alerts a
        SET    resolved_at = now()
        WHERE  a.resolved_at IS NULL
          AND  NOT EXISTS (
                   SELECT 1 FROM ingestion_health() h
                   WHERE  h.source_id = a.source_id
                     AND  h.kind      = a.kind
                     AND  h.status    = a.status
               )
        RETURNING a.source_id, a.kind, a.status
    )
    SELECT 'RESOLVED'::text, r.source_id, r.kind, r.status FROM resolved r;
END;
$$;

COMMENT ON FUNCTION app.detect_operational_alerts() IS
    'Turns ingestion_health() into alert rows, once per transition. Returns '
    'what it opened and resolved so a caller can log it.';

GRANT EXECUTE ON FUNCTION app.detect_operational_alerts() TO ii_app, ii_alerts;

-- ---------------------------------------------------------------------------
-- Ingestion metrics
-- ---------------------------------------------------------------------------
-- A view, not a table: pure aggregation over the run log, so materialising it
-- would give the staleness detector its own staleness problem.
CREATE VIEW ingestion_metrics AS
SELECT source_id,
       kind,
       date_trunc('day', started_at)::date          AS day,
       count(*)                                     AS runs,
       count(*) FILTER (WHERE outcome = 'SUCCESS')  AS succeeded,
       count(*) FILTER (WHERE outcome = 'PARTIAL')  AS partial,
       count(*) FILTER (WHERE outcome = 'FAILED')   AS failed,
       count(*) FILTER (WHERE outcome = 'RUNNING')  AS still_running,
       sum(rows_written)                            AS facts_written,
       sum(rows_rejected)                           AS rows_rejected,
       -- Duration matters as an early warning: a job whose runtime doubles is
       -- usually about to start timing out.
       round(avg(extract(epoch FROM finished_at - started_at))::numeric, 1)
                                                    AS avg_seconds,
       round(max(extract(epoch FROM finished_at - started_at))::numeric, 1)
                                                    AS max_seconds
FROM   ingestion_runs
GROUP  BY source_id, kind, date_trunc('day', started_at)::date;

COMMENT ON VIEW ingestion_metrics IS
    'Per source, kind and day. `still_running` is the interesting column: a '
    'run stuck in RUNNING is a process that died without finishing, which no '
    'outcome-based query would notice.';

GRANT SELECT ON ingestion_metrics TO ii_app, ii_ingest, ii_alerts;
