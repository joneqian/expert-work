"""tenant_mcp_server registry table + RLS — Stream V-B.

Revision ID: 0054_tenant_mcp_server
Revises: 0053_tenant_status
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0054_tenant_mcp_server"
down_revision: str | Sequence[str] | None = "0053_tenant_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "tenant_mcp_server"
_POLICY = "tenant_mcp_server_tenant_isolation"


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
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("transport", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("auth_type", sa.Text(), nullable=False, server_default=sa.text("'none'")),
        sa.Column("token_secret_ref", sa.Text(), nullable=True),
        sa.Column(
            "timeout_s",
            sa.Float(),
            nullable=False,
            server_default=sa.text("30"),
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "transport IN ('sse', 'streamable_http')",
            name="tenant_mcp_server_transport_check",
        ),
        sa.CheckConstraint(
            "auth_type IN ('none', 'bearer')",
            name="tenant_mcp_server_auth_type_check",
        ),
    )
    op.create_index("tenant_mcp_server_tenant_idx", _TABLE, ["tenant_id"])
    op.create_index(
        "tenant_mcp_server_name_uniq",
        _TABLE,
        ["tenant_id", "name"],
        unique=True,
    )

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"CREATE POLICY {_POLICY} ON {_TABLE} "
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);"
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE};")
    op.drop_table(_TABLE)
