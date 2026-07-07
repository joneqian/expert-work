# Audit-log restore from WORM backup — Stream K.K14

> Recovery procedure for the immutable audit-log backup the
> `audit-backup-worker` writes to S3 Object Lock (compliance mode).
> Use when the live `audit_log` table is destroyed, corrupted, or
> needs to be reconstructed for a compliance request that pre-dates a
> retention sweep.

## When to use this runbook

* `audit_log` table is dropped / truncated / restored from a bad snapshot
* CodeQL or auditor asks for `audit_log` rows beyond
  `tenant_config.audit_retention_days` (the live retention sweep
  hard-deletes those; the WORM backup keeps them for the bucket's
  compliance window — D.1 / Stream D §)
* Cross-AZ DR drill (M1) — verify the WORM bucket is the actual
  source of truth, not the live DB

## Pre-flight checks

1. **Confirm the WORM bucket is reachable** —
   `aws s3 ls s3://$EXPERT_WORK_AUDIT_BUCKET --recursive | head` (the
   compliance-locked objects still list normally; only `delete` /
   `put` of an existing key are rejected).
2. **Confirm the migration story** — `audit_log` migrations 0001 /
   0006 / 0008 / 0009 must be at the same revision as when the
   backup was written. A schema gap on `details` JSONB or
   `backup_acked` would have to be reconciled before insert.
3. **Identify the scope** — full restore vs single-tenant. The
   WORM bucket layout is
   `{tenant_id}/{YYYY}/{MM}/{DD}/{audit_id}.json` (see
   `audit_backup_worker.serialization.object_key_for`), so per-tenant
   restores are a prefix scan.

## Recovery steps

### Step 1 — provision a sibling table

```sql
CREATE TABLE audit_log_restored (LIKE audit_log INCLUDING ALL);
ALTER TABLE audit_log_restored ENABLE ROW LEVEL SECURITY;
-- 0005 RLS policy and the 0008 / 0009 column-level grants must be
-- replicated; copy them from migration scripts rather than hand-rolling.
```

Working on a sibling table keeps the live RBAC / RLS plumbing alive
while restore runs; the operator decides whether to swap names or
read across both in `Step 4`.

### Step 2 — run the restore tool

`tools/persistence/restore_audit.py` walks the bucket prefix and
hands each serialised row to a writer hook. The recommended
production binding is an INSERT against `audit_log_restored`:

```python
# Sketch — adapt to your env vars / DSN handling.
from expert_work.runtime.storage.s3_compatible import S3CompatibleObjectStore
from tools.persistence.restore_audit import restore_audit_rows
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

store = S3CompatibleObjectStore(...)  # production env wiring
engine = create_async_engine(os.environ["EXPERT_WORK_PG_DSN_RESTORE_ADMIN"])

INSERT_SQL = text(
    "INSERT INTO audit_log_restored ("
    "  id, tenant_id, actor_type, actor_id, on_behalf_of, action, "
    "  resource_type, resource_id, result, reason, ip, user_agent, "
    "  request_id, trace_id, details, occurred_at"
    ") VALUES ("
    "  :id, :tenant_id, :actor_type, :actor_id, :on_behalf_of, :action, "
    "  :resource_type, :resource_id, :result, :reason, :ip, :user_agent, "
    "  :request_id, :trace_id, CAST(:details AS jsonb), :occurred_at"
    ")"
    " ON CONFLICT (id) DO NOTHING"
)

async def writer(payload):
    async with engine.begin() as conn:
        await conn.execute(INSERT_SQL, payload)

report = await restore_audit_rows(object_store=store, prefix=tenant_prefix, writer=writer)
print(report)  # restored=..., failed_keys=(...)
```

### Step 3 — verify

* `SELECT count(*) FROM audit_log_restored;` matches `report.restored`
* `SELECT min(occurred_at), max(occurred_at) FROM audit_log_restored;`
  brackets the window the WORM bucket holds
* Spot-check a few rows against the originating S3 keys (the key
  carries `{audit_id}.json`)
* Investigate any `report.failed_keys` by hand (`aws s3 cp s3://.../$KEY -`)

### Step 4 — promote (operator decision)

Either:

**A. Swap names** (full replacement) —

```sql
BEGIN;
ALTER TABLE audit_log RENAME TO audit_log_pre_restore;
ALTER TABLE audit_log_restored RENAME TO audit_log;
COMMIT;
```

**B. Read across** (compliance answer only, keep current state) —
expose `audit_log_restored` to the auditor / oncall view only; no
DML against it. The retention sweep continues to operate against
`audit_log` and is untouched.

## Verifying the drill in CI

`tools/persistence/test_restore_audit.py` exercises the same code
path against an in-memory object store seeded by
`audit_backup_worker.serialization.serialize_row`. The three drill
tests pin:

* round-trip integrity (serialise → backup → restore)
* per-tenant prefix isolation
* corrupt-blob tolerance (the restore tool logs the bad key and
  keeps going so a single garbage object cannot halt recovery)

A regression in `serialize_row` or `object_key_for` would fail
these CI tests *before* anyone needs to run this runbook for real.

## Related runbooks

* [`postgres.md`](postgres.md) — base Postgres restore (the
  `audit_log_restored` table will need the same RLS / grant
  plumbing the migrations install).
* [`deployment.md`](deployment.md) — `tools/deploy/rollback.py` if
  the restore was triggered by a bad deploy.
