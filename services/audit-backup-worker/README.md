# expert-work-audit-backup-worker

Expert Work **audit WORM backup worker** — streams unacked `audit_log` rows
into an Object-Lock-enabled S3-compatible bucket and flips
`backup_acked = true`. Lands through [Stream D.1c](../../docs/streams/STREAM-D-DESIGN.md).

## Scope (D.1c)

- `SqlAuditBackupReader` selects unacked rows in id order via a partial
  index (`audit_log_backup_pending_idx` from migration 0008).
- Each row is serialised to JSON and `ObjectStore.put`'d with
  `lock_mode="compliance"` + `retain_until = now + retention_days`.
- After a successful put the worker `UPDATE`s `backup_acked = true` /
  `backup_acked_at = now()` — column-level grant from migration 0009.
- Failures keep the row unacked so the next sweep retries; persistent
  per-row failures get an exponential backoff so a poison row doesn't
  block the queue.

## Operational notes

- BYPASSRLS role `audit_backup_worker`; `SET LOCAL ROLE` on both the
  read and write transactions.
- Postgres connection should bypass PgBouncer transaction pooling
  because `SET LOCAL ROLE` must persist across the read and the
  subsequent UPDATE within the same transaction.
- `audit_retention_days_default` is global until D.3 lands the per-
  tenant `tenant_config.audit_retention_days` override.

## Entry point

```bash
uv run python -m audit_backup_worker
```

Settings: `EXPERT_WORK_AUDIT_BACKUP_*` env (see `settings.py`).
