-- 016 · "Never ran" and "never succeeded" are different diagnoses
--
-- Found by the Phase 8 tests. 015's CASE tested `last_success IS NULL` first,
-- so a job that had run several times and failed every time reported
-- NEVER_RAN.
--
-- That matters because the two lead to different actions. NEVER_RAN means look
-- at the scheduler: the workflow is disabled, the cron is wrong, the secret is
-- missing. NEVER_SUCCEEDED means look at the source: the job is firing fine
-- and the provider is refusing us. Reporting the first when it is the second
-- sends you to the wrong place.
--
-- The distinguishing evidence is `last_outcome`, which is non-null the moment
-- any run exists at all, successful or not.

CREATE OR REPLACE FUNCTION ingestion_health()
RETURNS TABLE (
    source_id    text,
    kind         text,
    max_age      interval,
    last_success timestamptz,
    age          interval,
    last_outcome text,
    status       text
)
LANGUAGE sql STABLE AS $$
    SELECT sch.source_id,
           sch.kind,
           sch.max_age,
           latest.last_success,
           now() - latest.last_success AS age,
           latest.last_outcome,
           CASE
               -- No run of any kind. Look at the scheduler.
               WHEN latest.last_outcome IS NULL          THEN 'NEVER_RAN'
               -- Runs happened, none worked. Look at the source.
               WHEN latest.last_success IS NULL          THEN 'NEVER_SUCCEEDED'
               WHEN now() - latest.last_success > sch.max_age THEN 'STALE'
               WHEN latest.last_outcome = 'FAILED'       THEN 'LAST_RUN_FAILED'
               WHEN latest.last_outcome = 'PARTIAL'      THEN 'LAST_RUN_PARTIAL'
               ELSE 'OK'
           END AS status
    FROM   ingestion_schedule sch
    LEFT   JOIN LATERAL (
               SELECT max(r.finished_at) FILTER (WHERE r.outcome = 'SUCCESS')
                        AS last_success,
                      (array_agg(r.outcome ORDER BY r.started_at DESC))[1]
                        AS last_outcome
               FROM   ingestion_runs r
               WHERE  r.source_id = sch.source_id
                 AND  r.kind      = sch.kind
           ) latest ON true
    WHERE  sch.enabled
    ORDER  BY sch.source_id, sch.kind;
$$;
