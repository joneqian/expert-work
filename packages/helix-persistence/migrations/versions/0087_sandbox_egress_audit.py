"""Per-agent sandbox egress — the egress-proxy audit table.

Revision ID: 0087_sandbox_egress_audit
Revises: 0086_tenant_skill_subscription
Create Date: 2026-06-21

``sandbox_egress_audit`` — one row per sandbox→internet connection through the
transparent egress proxy (sandbox-egress design §3.1). Records host + port +
byte volumes + verdict; never payload (HTTPS is tunnelled, the proxy sees only
``host:port``). This is the audit-over-blocking record: egress is allowed and
traced, not walled.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0087_sandbox_egress_audit"
down_revision: str | Sequence[str] | None = "0086_tenant_skill_subscription"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "sandbox_egress_audit",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=True),
        sa.Column("agent_version", sa.Text(), nullable=True),
        sa.Column("sandbox_id", sa.Text(), nullable=True),
        sa.Column("target_host", sa.Text(), nullable=False),
        sa.Column("target_port", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("bytes_up", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("bytes_down", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "sandbox_egress_audit_tenant_time_idx",
        "sandbox_egress_audit",
        ["tenant_id", sa.text("occurred_at DESC")],
    )
    op.create_index(
        "sandbox_egress_audit_host_idx",
        "sandbox_egress_audit",
        ["target_host"],
    )


def downgrade() -> None:
    op.drop_index("sandbox_egress_audit_host_idx", table_name="sandbox_egress_audit")
    op.drop_index("sandbox_egress_audit_tenant_time_idx", table_name="sandbox_egress_audit")
    op.drop_table("sandbox_egress_audit")
