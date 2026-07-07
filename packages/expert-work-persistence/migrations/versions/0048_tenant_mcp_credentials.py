"""Stream O — per-tenant MCP server credentials.

Revision ID: 0048_tenant_mcp_creds
Revises: 0047_tenant_credentials
Create Date: 2026-05-29

Adds 1 column to ``tenant_config`` for the Stream O MCP credentials
(Mini-ADR O-14):

* ``mcp_credentials`` (JSONB, default ``'{}'::jsonb``) — per-tenant MCP
  server credentials map (``{server_name: secret_ref}``). Counterpart
  to ``tool_credentials`` (the per-tool API key map) added in migration
  0047; ``server_name`` is platform-defined (never tenant input) and
  the secret_ref backs that server's bearer auth in ``tenant`` mode.

Default ``'{}'::jsonb`` so all existing rows keep current behavior.

Revision id ``0048_tenant_mcp_creds`` = 21 chars (within the 32-char
alembic ``version_num`` ceiling per
[memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0048_tenant_mcp_creds"
down_revision: str | Sequence[str] | None = "0047_tenant_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "tenant_config",
        sa.Column(
            "mcp_credentials",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_config", "mcp_credentials")
