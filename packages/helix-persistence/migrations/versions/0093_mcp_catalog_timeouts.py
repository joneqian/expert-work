"""mcp_connector_catalog timeout_s + sse_read_timeout_s (platform server tuning).

Stream MCP platform-servers (form polish). A platform admin can now tune the
shared server's request timeout and (for streaming transports) the SSE read
timeout — the per-read idle wait between events, independent of the connect/call
cap. NULL = use the orchestrator defaults. Mirrors the per-tenant custom-server
knobs so platform servers are not weaker than self-registered ones.

Revision ID: 0093_mcp_catalog_timeouts
Revises: 0092_mcp_catalog_bearer_token
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0093_mcp_catalog_timeouts"
down_revision: str | Sequence[str] | None = "0092_mcp_catalog_bearer_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "mcp_connector_catalog"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("timeout_s", sa.Float(), nullable=True))
    op.add_column(_TABLE, sa.Column("sse_read_timeout_s", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, "sse_read_timeout_s")
    op.drop_column(_TABLE, "timeout_s")
