"""Stream RT-5 (RT-ADR-24) — quality_score per-agent verdict time-series.

One LLM-judge verdict per sampled production run, per-tenant RLS. Tenant
isolation uses the standard ``app.tenant_id`` GUC pattern (same as
``agent_run`` / ``agent_disable``). ``UNIQUE (tenant_id, run_id)`` makes a
re-scan an idempotent ``ON CONFLICT DO NOTHING``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0117_quality_score"
down_revision: str | Sequence[str] | None = "0116_workspace_last_write"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "quality_score",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("agent_version", sa.Text(), nullable=False),
        sa.Column("run_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("overall", sa.Integer(), nullable=False),
        sa.Column("dimensions", JSONB(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("judge_model", sa.Text(), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", "run_id", name="quality_score_tenant_run_uniq"),
    )
    op.create_index(
        "quality_score_tenant_agent_time_idx",
        "quality_score",
        ["tenant_id", "agent_name", "observed_at"],
    )
    # Tenant RLS — same app.tenant_id GUC pattern as agent_run.
    op.execute("ALTER TABLE quality_score ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY quality_score_tenant_isolation ON quality_score "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS quality_score_tenant_isolation ON quality_score")
    op.drop_index("quality_score_tenant_agent_time_idx", table_name="quality_score")
    op.drop_table("quality_score")
