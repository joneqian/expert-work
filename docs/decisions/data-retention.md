# Data Retention SLA (M0)

Status: accepted (2026-05-14)
Stream: D.3
Related: [STREAM-D-DESIGN](../streams/STREAM-D-DESIGN.md) § 2.6 + Mini-ADR D-5; subsystems/17 § 5.3.

## Tables in scope

| Table | TTL source | WORM gate before delete | Notes |
|-------|------------|-------------------------|-------|
| `audit_log` | `tenant_config.audit_retention_days` (default 90, max 3650) | Yes — only `backup_acked = true` rows are eligible | Per-tenant. Compliance lock on the S3 backup is the durable copy. |
| `event_log` | `tenant_config.event_log_retention_days` (default 30, max 3650) | No (cold-archive pipeline is Stream G) | Per-tenant. M0 simply prunes; M1 will add cold archive. |
| `jwt_blacklist` | `expires_at` per row (no tenant column) | n/a | Global. Used by Stream C revocation. |
| `token_reservation` | Not handled here — already reaped by `ReservationReaper` (Stream C.5) | n/a | Stale `RESERVED` rows beyond `quota_reservation_max_age_s` are reaped on a separate cadence. |

## Operational SLA

- **Audit log retention** runs nightly via the `retention-cleanup-job`
  service. Per-tenant TTL changes take effect on the next sweep. The
  minimum legal retention is 1 day; admins set `audit_retention_days = 7`
  for short-lived dev tenants, `2555` (≈7 years) for HIPAA-style packs.
- **Skipped-unacked**: `audit_skipped_unacked > 0` is a warning, not a
  failure. Steady state is 0. A persistent non-zero value means the D.1c
  WORM-backup worker (`audit-backup-worker`) is falling behind; the
  cleanup job logs the count and the per-row backlog should resolve as
  the backup worker catches up.

## Privilege boundary

`retention_cleanup_worker` is the only role with DELETE on
`audit_log` / `event_log` / `jwt_blacklist` (migration 0010). The
control-plane main app role does NOT have DELETE — that preserves the
D.1a append-only contract at the DB layer. Production deployments
assign the role to a dedicated cron user distinct from the app's
runtime connection.

## What this SLA does NOT cover (M1+)

- **GDPR Article 17** ("right to erasure") — out-of-cycle per-subject
  deletion lives in Stream M1 GDPR endpoints; that workflow uses a
  different code path and creates an `audit_log` row attributing the
  deletion (which itself is retained per this SLA).
- **Cold archive of `event_log`** — Stream G adds the S3 pipeline and
  reading-from-archive query path; once that lands, `event_log` deletes
  in cleanup will be gated on `cold_archived = true` similar to today's
  `audit_log` / `backup_acked` gate.
- **Postgres partitioning** — Mini-ADR D-5 deferred this to M1. When
  it lands, the cleanup job switches from batched DELETE to partition
  detach + drop; the SLA shape does not change.
