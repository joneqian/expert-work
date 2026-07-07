"""Stream D.1c — ``audit_backup_worker`` role + column-level UPDATE grant.

Revision ID: 0009_audit_backup_worker_role
Revises: 0008_audit_writer_role
Create Date: 2026-05-14

D.1a (migration 0008) revoked ``UPDATE / DELETE / TRUNCATE`` on
``audit_log`` from ``PUBLIC`` so the log is append-only at the DB
layer. The D.1c WORM-backup worker needs to flip ``backup_acked``
true after it streams a row into the audit bucket — that's a tiny
column-level mutation that does NOT betray the append-only contract.

Postgres supports column-level ``GRANT UPDATE (col_a, col_b)``; we
use it here to give the worker exactly the privilege it needs:

* SELECT on ``audit_log`` (read unacked rows + serialize to JSON).
* UPDATE only on ``backup_acked`` + ``backup_acked_at`` (cannot
  touch tenant_id, action, details, etc.).

The role is BYPASSRLS for the same reason ``audit_writer`` /
``audit_reader`` are — the backup worker scans across every tenant.

The migration does NOT grant ``audit_backup_worker`` to any app
role: deployment owns that (one for prod via IaC, one inline in the
integration test fixture). See STREAM-D-DESIGN § 2.2.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_audit_backup_worker_role"
down_revision: str | Sequence[str] | None = "0008_audit_writer_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_backup_worker') THEN
                CREATE ROLE audit_backup_worker NOLOGIN BYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO audit_backup_worker;")
    op.execute("GRANT SELECT ON TABLE audit_log TO audit_backup_worker;")
    # Column-level UPDATE: only the two markers the worker writes.
    # Everything else stays under the 0008 ``REVOKE UPDATE FROM PUBLIC``.
    op.execute(
        "GRANT UPDATE (backup_acked, backup_acked_at) ON TABLE audit_log TO audit_backup_worker;"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE UPDATE (backup_acked, backup_acked_at) ON TABLE audit_log FROM audit_backup_worker;"
    )
    op.execute("REVOKE SELECT ON TABLE audit_log FROM audit_backup_worker;")
    op.execute("REVOKE USAGE ON SCHEMA public FROM audit_backup_worker;")
    op.execute("DROP ROLE IF EXISTS audit_backup_worker;")
