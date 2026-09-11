-- 023 · Qualify the RETURNING columns in detect_operational_alerts
--
-- 022 declared RETURNS TABLE (action, source_id, kind, status), which creates
-- plpgsql OUT variables with those names. Inside the function body they shadow
-- the identically named columns of `app.operational_alerts`, so an unqualified
-- `RETURNING source_id, kind, status` is ambiguous and the function raised on
-- first call.
--
-- Fixed by qualifying the RETURNING columns with the table name rather than by
-- adding `#variable_conflict use_column`. The pragma would work and would
-- silently change name resolution for the whole function body, which is a
-- large blast radius for a local problem -- and the next person to add a
-- variable here would inherit a rule they did not know about.
--
-- The alternative, prefixing every OUT parameter (out_source_id and so on),
-- would leak an implementation detail into the result shape every caller sees.

CREATE OR REPLACE FUNCTION app.detect_operational_alerts()
RETURNS TABLE (action text, source_id text, kind text, status text)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    WITH unhealthy AS (
        SELECT h.source_id AS src, h.kind AS knd, h.status AS sts,
               CASE h.status
                   WHEN 'NEVER_RAN'       THEN 'CRITICAL'
                   WHEN 'NEVER_SUCCEEDED' THEN 'CRITICAL'
                   WHEN 'STALE'           THEN 'CRITICAL'
                   ELSE 'WARNING'
               END AS sev,
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
               END AS dtl
        FROM   ingestion_health() h
        WHERE  h.status <> 'OK'
    ),
    opened AS (
        INSERT INTO app.operational_alerts
            (source_id, kind, status, detail, severity)
        SELECT u.src, u.knd, u.sts, u.dtl, u.sev
        FROM   unhealthy u
        WHERE  NOT EXISTS (
                   SELECT 1 FROM app.operational_alerts a
                   WHERE  a.source_id = u.src
                     AND  a.kind      = u.knd
                     AND  a.status    = u.sts
                     AND  a.resolved_at IS NULL
               )
        RETURNING app.operational_alerts.source_id,
                  app.operational_alerts.kind,
                  app.operational_alerts.status
    )
    SELECT 'OPENED'::text, o.source_id, o.kind, o.status FROM opened o;

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
