# event-log-archive-job — Stream G.8

Cold-archives aged `event_log` rows to S3-compatible object storage,
then deletes them from Postgres. A one-shot sweep (cron / K8s CronJob
drives scheduling); idempotent — running it twice is safe.

```bash
uv run python -m event_log_archive_job
```

## What it does

Per [STREAM-G-DESIGN § 2.4](../../docs/streams/STREAM-G-DESIGN.md):

1. Find `event_log` rows older than `archive_age_days`, grouped by
   `(tenant_id, thread_id, month)`.
2. Serialise each group to JSONL and `put` it to object storage at
   `event-log/{tenant_id}/{YYYY}/{MM}/{thread_id}.jsonl`.
3. After the `put` succeeds, `DELETE` the group's rows.

Archive-then-delete + a deterministic key make the sweep crash-safe: a
mid-run crash re-archives (overwrites the same key) and re-deletes on
the next run — rows are never lost.

## Configuration

Env prefix `EXPERT_WORK_EVENT_LOG_ARCHIVE_` — see `settings.py`. Key knobs:
`db_dsn`, `archive_age_days` (default 180), `object_store_backend`
(`s3-compatible` / `memory`), `s3_*`, `batch_size`.

## Notes

- **DB role**: M0 connects with the operator-supplied DSN (dev default
  is the superuser, which bypasses RLS — the job is cross-tenant). A
  dedicated least-privilege `event_log_archive_worker` role is prod
  hardening, deferred.
- **Manifest**: the deterministic S3 key layout + per-run summary log
  are the M0 "archive manifest". A queryable manifest table pairs with
  the M1-B transparent query-back path and is deferred.
