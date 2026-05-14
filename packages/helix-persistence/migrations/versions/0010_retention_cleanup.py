"""Stream D.3 — retention config columns + ``retention_cleanup_worker`` role.

Revision ID: 0010_retention_cleanup
Revises: 0009_audit_backup_worker_role
Create Date: 2026-05-14

Two pieces of D.3 in one migration:

1.  ``tenant_config.audit_retention_days`` (default 90) and
    ``tenant_config.event_log_retention_days`` (default 30). Both
    CHECK-constrained to (0, 3650] so a typo can't lock data forever
    or expire it immediately.

2.  ``retention_cleanup_worker`` NOLOGIN BYPASSRLS role with the
    minimum surface to delete expired rows:

    * SELECT on ``tenant_config`` (read per-tenant retention),
      ``audit_log`` / ``event_log`` (look up candidates),
      ``jwt_blacklist`` (global expiry).
    * DELETE on ``audit_log`` / ``event_log`` / ``jwt_blacklist``.

    The 0008 ``REVOKE DELETE FROM PUBLIC`` on ``audit_log`` is
    preserved — only this dedicated role gets DELETE back, and only
    after explicit grant. Production deployments assign membership
    of this role to a separate cron-driven account distinct from the
    app's main connection.

    The retention cleanup job runs *only* with
    ``WHERE backup_acked = true`` on ``audit_log``; deletes of
    unacked rows are application-guarded (test pins this).
    STREAM-D-DESIGN § 2.6 "Mini-ADR D-5".

The migration does NOT grant the role to any login user; deployment
+ test fixtures bind it explicitly.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_retention_cleanup"
down_revision: str | Sequence[str] | None = "0009_audit_backup_worker_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "tenant_config",
        sa.Column(
            "audit_retention_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("90"),
        ),
    )
    op.add_column(
        "tenant_config",
        sa.Column(
            "event_log_retention_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("30"),
        ),
    )
    op.create_check_constraint(
        "tenant_config_audit_retention_range_ck",
        "tenant_config",
        "audit_retention_days > 0 AND audit_retention_days <= 3650",
    )
    op.create_check_constraint(
        "tenant_config_event_retention_range_ck",
        "tenant_config",
        "event_log_retention_days > 0 AND event_log_retention_days <= 3650",
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'retention_cleanup_worker') THEN
                CREATE ROLE retention_cleanup_worker NOLOGIN BYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO retention_cleanup_worker;")
    # Per-table grants (multi-table forms have surprised us in CI;
    # splitting keeps the intent unambiguous + each statement gets its
    # own ACL update). Reads first, then the narrow DELETE set.
    op.execute("GRANT SELECT ON TABLE tenant_config TO retention_cleanup_worker;")
    op.execute("GRANT SELECT ON TABLE audit_log TO retention_cleanup_worker;")
    op.execute("GRANT SELECT ON TABLE event_log TO retention_cleanup_worker;")
    op.execute("GRANT SELECT ON TABLE jwt_blacklist TO retention_cleanup_worker;")
    op.execute("GRANT DELETE ON TABLE audit_log TO retention_cleanup_worker;")
    op.execute("GRANT DELETE ON TABLE event_log TO retention_cleanup_worker;")
    op.execute("GRANT DELETE ON TABLE jwt_blacklist TO retention_cleanup_worker;")


def downgrade() -> None:
    op.execute("REVOKE DELETE ON TABLE audit_log FROM retention_cleanup_worker;")
    op.execute("REVOKE DELETE ON TABLE event_log FROM retention_cleanup_worker;")
    op.execute("REVOKE DELETE ON TABLE jwt_blacklist FROM retention_cleanup_worker;")
    op.execute("REVOKE SELECT ON TABLE tenant_config FROM retention_cleanup_worker;")
    op.execute("REVOKE SELECT ON TABLE audit_log FROM retention_cleanup_worker;")
    op.execute("REVOKE SELECT ON TABLE event_log FROM retention_cleanup_worker;")
    op.execute("REVOKE SELECT ON TABLE jwt_blacklist FROM retention_cleanup_worker;")
    op.execute("REVOKE USAGE ON SCHEMA public FROM retention_cleanup_worker;")
    op.execute("DROP ROLE IF EXISTS retention_cleanup_worker;")

    op.drop_constraint(
        "tenant_config_event_retention_range_ck",
        "tenant_config",
        type_="check",
    )
    op.drop_constraint(
        "tenant_config_audit_retention_range_ck",
        "tenant_config",
        type_="check",
    )
    op.drop_column("tenant_config", "event_log_retention_days")
    op.drop_column("tenant_config", "audit_retention_days")
