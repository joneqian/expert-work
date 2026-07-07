"""Stream RT-5 (RT-ADR-24) — quality_drift_alert per-agent alert history.

Append-only per-tenant history of raised quality-drift alerts (cooldown source
+ dashboard list). Tenant RLS uses the standard ``app.tenant_id`` GUC pattern.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0118_quality_drift_alert"
down_revision: str | Sequence[str] | None = "0117_quality_score"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "quality_drift_alert",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("recent_mean", sa.Float(), nullable=False),
        sa.Column("baseline_mean", sa.Float(), nullable=False),
        sa.Column("drift_pct", sa.Float(), nullable=False),
        sa.Column("recent_count", sa.Integer(), nullable=False),
        sa.Column("baseline_count", sa.Integer(), nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "quality_drift_alert_tenant_agent_time_idx",
        "quality_drift_alert",
        ["tenant_id", "agent_name", "detected_at"],
    )
    # Tenant RLS — same app.tenant_id GUC pattern as agent_run.
    op.execute("ALTER TABLE quality_drift_alert ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY quality_drift_alert_tenant_isolation ON quality_drift_alert "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS quality_drift_alert_tenant_isolation ON quality_drift_alert")
    op.drop_index("quality_drift_alert_tenant_agent_time_idx", table_name="quality_drift_alert")
    op.drop_table("quality_drift_alert")
