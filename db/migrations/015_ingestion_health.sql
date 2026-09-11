-- 015 · Ingestion health, as a query
--
-- Architecture §4 lists "scheduler never fires" as a failure mode whose
-- symptom is indistinguishable from "nothing to do". ADR 004 then chose
-- GitHub Actions, which has exactly that failure: scheduled workflows are
-- silently disabled after 60 days of repository inactivity.
--
-- The mitigation was designed before the tool was picked: expected-run gap
-- detection over the run log. This is that, as a function, so the same
-- definition serves the CLI, the monitoring in Phase 16, and the freshness
-- banner in the UI. Three consumers of one definition beats three
-- definitions.
--
-- The key design point is that this reports on an EXPECTATION, not on rows.
-- A query over `ingestion_runs` alone can only tell you about runs that
-- happened. The interesting question is the absence of one.

CREATE TABLE ingestion_schedule (
    source_id       text        NOT NULL REFERENCES sources (source_id),
    kind            text        NOT NULL,
    -- How often we expect a successful run. A daily job that has not
    -- succeeded in three days is a problem worth waking someone for; one
    -- that is four hours late is not.
    max_age         interval    NOT NULL,
    enabled         boolean     NOT NULL DEFAULT true,
    note            text,
    PRIMARY KEY (source_id, kind)
);

COMMENT ON TABLE ingestion_schedule IS
    'What we EXPECT to run, and how stale is too stale. Without this, a '
    'scheduler that stops firing is invisible: there is no row to look at, '
    'and no absence to notice.';

CREATE FUNCTION ingestion_health()
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
               WHEN latest.last_success IS NULL          THEN 'NEVER_RAN'
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

COMMENT ON FUNCTION ingestion_health() IS
    'One row per expected job. NEVER_RAN and STALE are the two that a run-log '
    'query alone cannot produce, and they are the ones that matter: both mean '
    'the site is serving confidently stale numbers.';

GRANT SELECT ON ingestion_schedule TO ii_app, ii_ingest;
GRANT INSERT, UPDATE ON ingestion_schedule TO ii_ingest;
GRANT EXECUTE ON FUNCTION ingestion_health() TO ii_app, ii_ingest;
