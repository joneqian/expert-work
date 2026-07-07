"""Stream D.1a — ``audit_writer`` role + ``backup_acked`` columns.

Revision ID: 0008_audit_writer_role
Revises: 0007_tenant_config
Create Date: 2026-05-14

Two pieces of D.1a in one migration (they're tightly coupled — both
serve the WORM-backup pipeline that D.1c builds on):

1.  Add ``backup_acked`` BOOL DEFAULT FALSE and ``backup_acked_at``
    TIMESTAMPTZ to ``audit_log``, plus a **partial index** keyed on
    ``occurred_at`` WHERE ``backup_acked = false``. The partial index
    keeps the backup worker's "find next batch" scan O(unacked-count)
    rather than O(total-rows) — by D.3 retention cleanup the acked
    rows leave the index entirely.

2.  Create a NOLOGIN ``audit_writer`` role with **INSERT + SELECT
    only** on ``audit_log``. Revoke ``UPDATE`` / ``DELETE`` /
    ``TRUNCATE`` from PUBLIC so even a compromised app role cannot
    quietly mutate or clear audit history. The role is also
    ``BYPASSRLS`` — mirroring the existing ``audit_reader`` from
    0005. The audit write path legitimately inserts rows for every
    tenant the application serves; under RLS+FORCE the per-row
    ``WITH CHECK (tenant_id = current_setting('app.tenant_id'))``
    policy would otherwise require us to thread the GUC into every
    audit write site even though the writer is trusted, single-
    purpose, and not a tenant-scoped data path.

The migration does NOT grant ``audit_writer`` to any app role — that
grant lives in deployment fixtures (one for prod, one inline in the
integration test). Migration is content-only; ops decides who may
assume the role. See STREAM-D-DESIGN § 2.2 + Mini-ADR D-1.

The B.1c WORM-backup worker (Stream D.1c) is the only thing that
flips ``backup_acked`` to ``true``. Until then every row sits at
``false``, the partial index just grows, and D.3 retention cleanup
refuses to delete unacked rows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_audit_writer_role"
down_revision: str | Sequence[str] | None = "0007_tenant_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "audit_log",
        sa.Column(
            "backup_acked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "audit_log",
        sa.Column("backup_acked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "audit_log_backup_pending_idx",
        "audit_log",
        ["occurred_at"],
        postgresql_where=sa.text("backup_acked = false"),
    )

    # Role + grants. Idempotent — alembic stamping in an existing dev
    # database must not break on second apply, matching the pattern
    # 0005_rls_baseline established for ``audit_reader``.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_writer') THEN
                CREATE ROLE audit_writer NOLOGIN BYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO audit_writer;")
    op.execute("GRANT INSERT, SELECT ON TABLE audit_log TO audit_writer;")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE audit_log_id_seq TO audit_writer;")

    # Lock down the table for everyone else. UPDATE / DELETE / TRUNCATE
    # on audit_log is the single most attractive target for "cover my
    # tracks" — the REVOKE FROM PUBLIC is what makes the audit append-
    # only at the DB layer. Application code that needs to insert MUST
    # ``SET LOCAL ROLE audit_writer`` inside the transaction.
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_log FROM PUBLIC;")


def downgrade() -> None:
    # Restore the previous permission shape before dropping the role
    # so that a re-up of 0008 lands cleanly.
    op.execute("GRANT UPDATE, DELETE, TRUNCATE ON TABLE audit_log TO PUBLIC;")
    op.execute("REVOKE ALL ON SEQUENCE audit_log_id_seq FROM audit_writer;")
    op.execute("REVOKE ALL ON TABLE audit_log FROM audit_writer;")
    op.execute("REVOKE USAGE ON SCHEMA public FROM audit_writer;")
    op.execute("DROP ROLE IF EXISTS audit_writer;")

    op.drop_index("audit_log_backup_pending_idx", table_name="audit_log")
    op.drop_column("audit_log", "backup_acked_at")
    op.drop_column("audit_log", "backup_acked")
