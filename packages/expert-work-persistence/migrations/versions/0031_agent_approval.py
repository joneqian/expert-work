"""Stream J.8-step3a — ``agent_approval`` table for HITL approvals.

Revision ID: 0031_agent_approval
Revises: 0030_artifact_lifecycle
Create Date: 2026-05-22

Adds the registry that records every run paused for human approval
(Mini-ADR J-24, STREAM-J-DESIGN § 14). expert_work's ``RunManager`` is
in-memory only (the ``runs`` table is M1+ work), so a paused run has
no persistent home — without one the 24h timeout job has nothing to
scan, ``GET`` cannot surface ``pending_approval`` after a control-plane
restart, and ``POST .../resume`` cannot find the run. This table is
the J.8-scoped slice of that future ``runs`` table.

* ``status = 'pending'`` — the run is paused, awaiting a verdict.
* ``status IN ('approved', 'rejected', 'modified', 'timeout')`` — a
  terminal verdict was recorded (by the resume endpoint or the
  timeout job).

A partial index on ``(timeout_at)`` over ``status = 'pending'`` makes
the timeout job's sweep O(log N). Tenant RLS uses the standard
``current_setting('app.tenant_id')`` GUC pattern.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0031_agent_approval"
down_revision: str | Sequence[str] | None = "0030_artifact_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_STATUS_VALUES = "('pending', 'approved', 'rejected', 'modified', 'timeout')"


def upgrade() -> None:
    op.create_table(
        "agent_approval",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("node", sa.Text(), nullable=False),
        sa.Column("reason_kind", sa.Text(), nullable=False),
        sa.Column("action_summary", sa.Text(), nullable=False),
        sa.Column("proposed_args", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timeout_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("decided_by", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("modified_args", JSONB(), nullable=True),
        sa.CheckConstraint(f"status IN {_STATUS_VALUES}", name="agent_approval_status_valid"),
        # A run pauses at most once at a time — one pending row per run.
        sa.UniqueConstraint("run_id", name="agent_approval_run_uniq"),
    )
    op.create_index("ix_agent_approval_tenant_id", "agent_approval", ["tenant_id"])
    op.create_index("ix_agent_approval_thread_id", "agent_approval", ["thread_id"])
    # Partial index — the 24h timeout sweep scans only pending rows.
    op.create_index(
        "ix_agent_approval_pending_timeout",
        "agent_approval",
        ["timeout_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    # Tenant RLS — same ``app.tenant_id`` GUC pattern as image_upload,
    # artifact, memory_item, user_workspace.
    op.execute("ALTER TABLE agent_approval ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY agent_approval_tenant_isolation ON agent_approval "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS agent_approval_tenant_isolation ON agent_approval")
    op.drop_index("ix_agent_approval_pending_timeout", table_name="agent_approval")
    op.drop_index("ix_agent_approval_thread_id", table_name="agent_approval")
    op.drop_index("ix_agent_approval_tenant_id", table_name="agent_approval")
    op.drop_table("agent_approval")
