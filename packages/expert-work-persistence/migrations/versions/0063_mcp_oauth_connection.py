"""mcp_oauth_connection per-user table + RLS — Stream MCP-OAUTH (OA-1b).

Per-user OAuth 2.1 connections to hosted MCP connectors. Tenant isolation via
RLS (same strict-equality policy as ``tenant_mcp_server``); user-level scoping is
enforced in the store. ``catalog_id`` FK CASCADE: deleting a catalog entry drops
its connections. Unique ``(tenant_id, user_id, catalog_id)`` — one connection per
user per connector.

Revision ID: 0063_mcp_oauth_connection
Revises: 0062_mcp_catalog_oauth
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0063_mcp_oauth_connection"
down_revision: str | Sequence[str] | None = "0062_mcp_catalog_oauth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "mcp_oauth_connection"
_POLICY = "mcp_oauth_connection_tenant_isolation"


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
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column(
            "catalog_id",
            UUID(as_uuid=True),
            sa.ForeignKey("mcp_connector_catalog.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("resolved_url", sa.Text(), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("access_token_ref", sa.Text(), nullable=True),
        sa.Column("refresh_token_ref", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("oauth_state", sa.Text(), nullable=True),
        sa.Column("pkce_verifier", sa.Text(), nullable=True),
        sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('pending', 'connected', 'expired', 'revoked', 'error')",
            name="mcp_oauth_connection_status_check",
        ),
    )
    op.create_index("mcp_oauth_connection_tenant_user_idx", _TABLE, ["tenant_id", "user_id"])
    op.create_index(
        "mcp_oauth_connection_uniq",
        _TABLE,
        ["tenant_id", "user_id", "catalog_id"],
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
