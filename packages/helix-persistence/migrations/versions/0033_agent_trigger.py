"""Stream J.10 — ``agent_trigger`` + ``trigger_run`` tables (Mini-ADR J-26 / J-42).

Revision ID: 0033_agent_trigger
Revises: 0032_agent_run
Create Date: 2026-05-22

J.10 调度 / 触发 (STREAM-J-DESIGN § 16). Two tables:

* ``agent_trigger`` — a registered cron / webhook trigger. The
  scheduler polls it (Mini-ADR J-42 — no APScheduler); a partial index
  over ``kind = 'cron' AND enabled`` keeps the scan cheap. A trigger
  name is unique per ``(tenant, agent)``.
* ``trigger_run`` — one firing of a trigger. Links to the ``agent_run``
  it started; carries the DLQ retry state (``attempt`` /
  ``next_retry_at`` / ``status``). A partial index over
  ``status = 'retrying'`` keeps the DLQ re-fire sweep cheap.

Both tables use the standard ``current_setting('app.tenant_id')`` GUC
tenant RLS. ``agent_trigger`` avoids the SQL reserved word ``trigger``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0033_agent_trigger"
down_revision: str | Sequence[str] | None = "0032_agent_run"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_KIND_VALUES = "('cron', 'webhook')"
_SOURCE_VALUES = "('manifest', 'api')"
_RUN_STATUS_VALUES = "('fired', 'succeeded', 'failed', 'retrying', 'dead_letter')"


def upgrade() -> None:
    op.create_table(
        "agent_trigger",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("agent_version", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("config", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("webhook_secret_hash", sa.Text(), nullable=True),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"kind IN {_KIND_VALUES}", name="agent_trigger_kind_valid"),
        sa.CheckConstraint(f"source IN {_SOURCE_VALUES}", name="agent_trigger_source_valid"),
        # A trigger name is unique per (tenant, agent) — the manifest
        # reconciliation key.
        sa.UniqueConstraint("tenant_id", "agent_name", "name", name="agent_trigger_name_uniq"),
    )
    op.create_index("ix_agent_trigger_tenant_id", "agent_trigger", ["tenant_id"])
    # Partial index — the scheduler scans only enabled cron triggers.
    op.create_index(
        "ix_agent_trigger_cron_enabled",
        "agent_trigger",
        ["kind", "enabled"],
        postgresql_where=sa.text("kind = 'cron' AND enabled = true"),
    )

    op.create_table(
        "trigger_run",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("trigger_id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'fired'")),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"status IN {_RUN_STATUS_VALUES}", name="trigger_run_status_valid"),
    )
    op.create_index("ix_trigger_run_tenant_id", "trigger_run", ["tenant_id"])
    op.create_index("ix_trigger_run_trigger_id", "trigger_run", ["trigger_id"])
    # Partial index — the DLQ re-fire sweep scans only retrying rows.
    op.create_index(
        "ix_trigger_run_retrying",
        "trigger_run",
        ["next_retry_at"],
        postgresql_where=sa.text("status = 'retrying'"),
    )

    # Tenant RLS — same ``app.tenant_id`` GUC pattern as agent_approval,
    # agent_run, image_upload, artifact.
    for table in ("agent_trigger", "trigger_run"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in ("trigger_run", "agent_trigger"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.drop_index("ix_trigger_run_retrying", table_name="trigger_run")
    op.drop_index("ix_trigger_run_trigger_id", table_name="trigger_run")
    op.drop_index("ix_trigger_run_tenant_id", table_name="trigger_run")
    op.drop_table("trigger_run")
    op.drop_index("ix_agent_trigger_cron_enabled", table_name="agent_trigger")
    op.drop_index("ix_agent_trigger_tenant_id", table_name="agent_trigger")
    op.drop_table("agent_trigger")
