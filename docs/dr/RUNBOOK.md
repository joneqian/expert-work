# Postgres Restore Runbook (M0)

Operator-facing procedure for restoring Helix-Agent's Postgres from a
``PostgresFullBackup`` artifact. Implements the recovery side of
[subsystems/22 § 4.2](../architecture/subsystems/22-disaster-recovery.md#42-恢复执行命令行--runbook).

> **M0 targets:** RPO ≤ 24 h, RTO ≤ 4 h. M1 brings PITR (WAL-G) and tightens to 15 min / 1 h.

## When to use this runbook

- Primary Postgres became unrecoverable (disk loss, corrupt schema, accidental ``DROP``).
- DR drill (quarterly per § 5.4 — operator initiates the same procedure in a staging-dr database).

If the issue is *application-level* (bad migration, but data intact),
prefer rolling back the migration over restoring from backup.

## Pre-flight (5 min)

1. **Identify the target backup**:

   ```sql
   SELECT id, asset_ref, started_at, finished_at, size_bytes, sha256, status
   FROM backup_record
   WHERE asset_type = 'postgres_full' AND status = 'success'
   ORDER BY started_at DESC
   LIMIT 5;
   ```

   Pick the most recent ``success`` row whose ``started_at`` predates the
   incident — restoring forward of the incident may re-import the bad state.

2. **Verify the artifact exists** in object storage. For MinIO local dev:

   ```bash
   mc ls local/helix-agent-dev/backups/postgres/ | head
   ```

   For Aliyun OSS prod, use the console or ``ossutil``.

3. **Verify SHA-256** matches the ``backup_record`` row before trusting the artifact:

   ```bash
   mc cp local/helix-agent-dev/<asset_ref> /tmp/restore.dump
   sha256sum /tmp/restore.dump
   # compare against backup_record.sha256
   ```

   A mismatch means the artifact has been tampered with or corrupted —
   **do not restore**. Investigate the storage layer first.

4. **Acquire step-up auth** for the production restore. Per § 4.2 D4,
   the operator must complete a fresh admin MFA challenge. The
   ``dr:restore`` audit row is written *before* restore begins (M0:
   operator inserts it manually; M1+ ``RestoreCommand`` writes it).

## Restore procedure

### Local dev / dogfood (RTO target: 30 min)

```bash
# 1. Make sure the application is *not* writing to the target DB.
docker compose stop      # if everything is in compose
# OR: revoke connect permissions on the target DB.

# 2. Recreate the target database empty.
docker exec -it helix-postgres psql -U helix_agent -d postgres \
    -c "DROP DATABASE IF EXISTS helix_agent_dev;"
docker exec -it helix-postgres psql -U helix_agent -d postgres \
    -c "CREATE DATABASE helix_agent_dev OWNER helix_agent;"

# 3. Re-run the bootstrap init script (creates extensions + sets timeouts).
docker exec -it helix-postgres psql -U helix_agent -d helix_agent_dev \
    -f /docker-entrypoint-initdb.d/00-helix-init.sql

# 4. Restore the dump. -Fc is the format pg_dump used; pg_restore reads it
#    streaming, no need to load the file into Postgres memory.
docker cp /tmp/restore.dump helix-postgres:/tmp/restore.dump
docker exec -it helix-postgres pg_restore \
    -U helix_agent \
    -d helix_agent_dev \
    --no-owner --no-privileges \
    --exit-on-error \
    /tmp/restore.dump

# 5. Smoke check (subsystems/22 § 5.3 verification list — minimal subset):
docker exec -it helix-postgres psql -U helix_agent -d helix_agent_dev -c "
    SELECT
      (SELECT count(*) FROM event_log)   AS events,
      (SELECT count(*) FROM thread_meta) AS threads,
      (SELECT count(*) FROM audit_log)   AS audits;
"
```

### Production / staging-dr

Replace the docker-exec calls with direct ``psql`` / ``pg_restore``
against the Aliyun RDS endpoint. The flow is identical:

1. Block writes (revoke ``CONNECT`` or stop app workers).
2. Recreate the DB empty.
3. Re-apply ``00-helix-init.sql`` (extensions + timeouts — RDS doesn't run init scripts).
4. ``pg_restore --no-owner --no-privileges --exit-on-error``.
5. Smoke check + reconcile ``backup_record`` (insert a row noting the restore source).

## Post-restore verification

Run before allowing application traffic back in:

| Check | Command | Expected |
|---|---|---|
| Migration head | ``alembic current`` | matches latest migration in repo |
| Row counts vs source | compare ``event_log`` / ``audit_log`` counts to dashboard | within ±1% (§ 5.3) |
| Indexes present | ``\\di`` in psql | every index from migrations exists |
| RLS readiness | ``SELECT count(*) FROM pg_policies;`` | M1+: matches expected policy count |

## After: write the audit row

Per subsystems/22 § 4.2 D4, every restore writes a ``dr:restore`` audit
row. M0 inserts manually until ``RestoreCommand`` lands (M1):

```sql
INSERT INTO audit_log (
    tenant_id, actor_type, actor_id,
    action, resource_type, resource_id,
    result, details, occurred_at
) VALUES (
    '<actor's home tenant uuid>',
    'user',
    '<operator-id>',
    'audit:read',          -- TODO: switch to dr:restore once the enum is extended
    'audit',
    '<backup_record.asset_ref>',
    'success',
    jsonb_build_object(
        'restore_target', 'helix_agent_dev',
        'source_asset_ref', '<asset_ref>',
        'source_sha256', '<sha>',
        'duration_s', <seconds>
    ),
    now()
);
```

> **Known M0 gap:** ``AuditAction`` enum does not yet include ``dr:restore`` /
> ``dr:failover`` / ``dr:drill``. The Stream D batch that handles AuditAction
> word-list expansion will add these. Until then, document the action in
> ``details.note`` and use ``audit:read`` as a placeholder.

## Known M0 limitations

- **No PITR.** The latest restore is only as fresh as the last successful daily ``pg_dump``. WAL-G + continuous WAL archiving lands in M1.
- **Single-region.** No cross-AZ replica to fail over to; restore is the only path.
- **Manual drill.** Quarterly drill is run by hand against staging-dr; the ``DrillRunner`` programmatic version is M1.
- **In-memory dump.** ``PostgresFullBackup`` loads the dump into memory before uploading — acceptable for dogfood (<2 GB DB) but will hit OOM at scale. Multipart streaming is the M1 multipart-upload work in ``ObjectStore``.
