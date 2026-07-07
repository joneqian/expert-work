-- Expert Work Postgres bootstrap (Stream A.3).
--
-- Executed by the official Postgres entrypoint on the first container start
-- (when /var/lib/postgresql/data is empty). Mounted as a read-only volume in
-- docker-compose.yml. Production / 阿里云 RDS deployments must run the
-- equivalent statements manually (RDS bypasses docker-entrypoint-initdb.d).
--
-- Design: subsystems/23-postgres-scalability.md § 9 (M0 deliverables).

-- Extensions ----------------------------------------------------------------
-- pg_stat_statements: enable slow-query forensics (also requires
--   shared_preload_libraries=pg_stat_statements, set in docker-compose.yml).
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- pgvector: required for memory_long_term (M1) but install the extension
--   upfront so M0 dev environments match prod and migrations don't surprise
--   us later.
CREATE EXTENSION IF NOT EXISTS vector;

-- Note: pg_partman extension installation is deferred to M1 alongside the
-- actual event_log partitioning rollout (§ 9 M1). Installing it now without
-- using it is dead weight; the runbook updates pg_partman setup at that point.

-- Database-wide safety defaults --------------------------------------------
-- These ALTER DATABASE statements set the default for new connections;
-- per-session SET LOCAL can still override for legitimate long-runners.
-- The Postgres docker-entrypoint does not expose POSTGRES_DB as a psql var,
-- so use current_database() — psql is already connected to the target DB.
--
-- - statement_timeout caps any single query (DoS mitigation, § 6).
-- - idle_in_transaction_session_timeout kills leaked transactions that
--   would otherwise hold row locks indefinitely (§ 6 replica-lag row).
-- - lock_timeout prevents DDL/migrations from queueing behind long DML
--   forever; expand-contract migrations rely on this (§ 6 锁表 row).
DO $$
DECLARE
    db TEXT := current_database();
BEGIN
    EXECUTE format('ALTER DATABASE %I SET statement_timeout = %L', db, '30s');
    EXECUTE format('ALTER DATABASE %I SET idle_in_transaction_session_timeout = %L', db, '60s');
    EXECUTE format('ALTER DATABASE %I SET lock_timeout = %L', db, '5s');
END $$;
