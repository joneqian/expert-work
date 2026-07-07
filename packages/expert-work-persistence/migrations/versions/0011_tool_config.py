"""Stream E.8 / E.9 — tenant_config tool-allowlist columns.

Revision ID: 0011_tool_config
Revises: 0010_retention_cleanup
Create Date: 2026-05-15

Adds two JSONB columns to ``tenant_config``:

* ``http_tool_allowlist`` (E.8): list of glob URL patterns the HTTP
  tool is permitted to call for this tenant. Default ``[]`` ↔
  deny-all so a freshly-provisioned tenant is safe until the admin
  explicitly opens an endpoint.

* ``mcp_servers`` (E.9; lands here so we don't churn migration 0012):
  list of ``{"name": str, "command": [str, ...], "env": {str: str}}``
  describing the MCP servers the orchestrator may launch via stdio
  for this tenant. Default ``[]`` ↔ no MCP. Schema validation lives
  in :class:`expert_work.protocol.TenantConfigPatch`, not in the DB,
  since JSONB CHECK constraints don't compose well with multi-row
  shape rules.

Both columns are ``NOT NULL DEFAULT '[]'::jsonb`` so existing
``tenant_config`` rows backfill silently.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0011_tool_config"
down_revision: str | Sequence[str] | None = "0010_retention_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "tenant_config",
        sa.Column(
            "http_tool_allowlist",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "tenant_config",
        sa.Column(
            "mcp_servers",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_config", "mcp_servers")
    op.drop_column("tenant_config", "http_tool_allowlist")
