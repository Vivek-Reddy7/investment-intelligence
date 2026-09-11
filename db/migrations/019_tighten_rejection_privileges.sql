-- 019 · Rejections are a record, not a cache
--
-- 018 granted INSERT and DELETE on `ingestion_rejections` out of habit,
-- following the pattern of `metric_values`. The DELETE-allow-list test caught
-- it and made the difference explicit, which is the whole point of keeping
-- that test as an allow-list rather than deleting it.
--
-- `metric_values` is genuinely a cache: every row is recomputable from the
-- facts by calling metrics_as_of(), so replacing it wholesale loses nothing.
--
-- `ingestion_rejections` is not. It records what a particular run refused and
-- why. That is not derivable from the facts -- by definition, the rejected
-- items are the ones that never became facts. Deleting a rejection destroys
-- the only evidence that we ever saw the data and declined it, which is
-- exactly the kind of quiet history loss this project is built against.
--
-- So: insert-only. If rejections ever need pruning, it will be a deliberate
-- retention policy with a migration, not an incidental privilege.

REVOKE DELETE ON ingestion_rejections FROM ii_ingest;

COMMENT ON TABLE ingestion_rejections IS
    'Insert-only record of what ingestion refused. Not a cache: rejected '
    'items never became facts, so nothing here is recomputable from them.';
