"""Stream J.8 closeout follow-up (Mini-ADR J-41) — ``agent_run`` table.

Revision ID: 0032_agent_run
Revises: 0031_agent_approval
Create Date: 2026-05-22

The durable run-lifecycle registry. helix's ``RunManager`` is in-memory
only with a 5-minute TTL (``cleanup(delay=300)``); without a persistent
row ``GET .../runs/{id}`` returns 404 for any non-paused run older than
5 minutes even though its contract promises "a run's status".

Mini-ADR J-41 splits the deferred "runs table" into (1) this *bare run
lifecycle layer* (M0) and (2) run queueing / multitask strategy / retry
/ DLQ (J.10, Mini-ADR J-26). The queueing columns are intentionally NOT
present — J.10 adds them via expand-contract when their design lands.

``RunManager`` mirror-writes every create / status transition here;
``GET`` reads it when the in-memory record has expired. The partial
index over ``status IN ('pending', 'running')`` keeps the in-flight
lookup cheap. Tenant RLS uses the standard
``current_setting('app.tenant_id')`` GUC pattern.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0032_agent_run"
down_revision: str | Sequence[str] | None = "0031_agent_approval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_STATUS_VALUES = "('pending', 'running', 'success', 'error', 'timeout', 'interrupted', 'paused')"
_DISCONNECT_VALUES = "('cancel', 'continue')"


def upgrade() -> None:
    op.create_table(
        "agent_run",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("thread_id", UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("on_disconnect", sa.Text(), nullable=False),
        sa.Column("is_resume", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"status IN {_STATUS_VALUES}", name="agent_run_status_valid"),
        sa.CheckConstraint(
            f"on_disconnect IN {_DISCONNECT_VALUES}", name="agent_run_on_disconnect_valid"
        ),
    )
    op.create_index("ix_agent_run_tenant_id", "agent_run", ["tenant_id"])
    op.create_index("ix_agent_run_thread_id", "agent_run", ["thread_id"])
    # Partial index — list_by_thread / has_inflight scan only live runs.
    op.create_index(
        "ix_agent_run_thread_inflight",
        "agent_run",
        ["thread_id", "status"],
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )

    # Tenant RLS — same ``app.tenant_id`` GUC pattern as agent_approval,
    # image_upload, artifact.
    op.execute("ALTER TABLE agent_run ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY agent_run_tenant_isolation ON agent_run "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS agent_run_tenant_isolation ON agent_run")
    op.drop_index("ix_agent_run_thread_inflight", table_name="agent_run")
    op.drop_index("ix_agent_run_thread_id", table_name="agent_run")
    op.drop_index("ix_agent_run_tenant_id", table_name="agent_run")
    op.drop_table("agent_run")
