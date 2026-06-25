"""agent_instance — per-(tenant, agent_code, end-user) binding.

Stream Agent-Templates (M1-5b). A lightweight anchor recording that an end-user
(``tenant_user.id``) uses a tenant agent (``agent_code`` = the fork's name). It is
NOT a copy of the agent — the agent definition is shared; this row + the per-user
memory / workspace / threads are the per-user "instance". Lets the platform
enumerate which end-users use an agent and track per-user last-active.

Tenant-scoped RLS (canonical tenant-isolation policy). Unique
``(tenant_id, agent_code, user_id)``.

Revision ID: 0097_agent_instance
Revises: 0096_token_usage_user_id
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0097_agent_instance"
down_revision: str | Sequence[str] | None = "0096_token_usage_user_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "agent_instance"
_POLICY = "agent_instance_tenant_isolation"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("agent_code", sa.Text(), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id", "agent_code", "user_id", name="agent_instance_identity_uniq"
        ),
    )
    op.create_index("agent_instance_tenant_agent_idx", _TABLE, ["tenant_id", "agent_code"])
    op.create_index("agent_instance_tenant_user_idx", _TABLE, ["tenant_id", "user_id"])

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE};")
    op.execute(
        f"""
        CREATE POLICY {_POLICY} ON {_TABLE}
            USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE};")
    op.drop_index("agent_instance_tenant_user_idx", table_name=_TABLE)
    op.drop_index("agent_instance_tenant_agent_idx", table_name=_TABLE)
    op.drop_table(_TABLE)
