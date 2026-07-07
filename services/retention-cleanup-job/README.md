# expert-work-retention-cleanup-job

Expert Work **retention cleanup job** — deletes expired rows from
`audit_log` / `event_log` / `jwt_blacklist` according to per-tenant
retention configured in `tenant_config`. Stream D.3.

## Scope (D.3)

- `audit_log` — only rows that are **already backed up** (`backup_acked = true`).
  Unacked rows are skipped + counted; persistent skips mean the D.1c
  WORM backup worker is falling behind and need attention.
- `event_log` — by `created_at` only (no WORM in M0).
- `jwt_blacklist` — by `expires_at` only (global, no tenant_id).

Per-tenant retention comes from `tenant_config.audit_retention_days`
(default 90) and `tenant_config.event_log_retention_days` (default 30).

## DB role

Runs as `retention_cleanup_worker` (NOLOGIN BYPASSRLS, created in
migration 0010) — column-narrow DELETE grants on the three target
tables; SELECT on `tenant_config`. The main app role does NOT have
DELETE, which preserves the D.1a append-only contract.

## Entry point

```bash
uv run python -m retention_cleanup_job
```

Default mode runs one sweep and exits — meant to be wired into cron
or a Kubernetes CronJob. Settings via `EXPERT_WORK_RETENTION_*` env (see
`settings.py`).
