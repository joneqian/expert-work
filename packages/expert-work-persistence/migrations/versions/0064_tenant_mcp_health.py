"""tenant_mcp_server connectivity-health columns (#2).

Persist the latest connectivity probe on each tenant MCP server row:
``last_probe_at`` / ``last_probe_status`` / ``last_probe_error``. All NULL until
the server is first probed. Health is observational — it never gates tool
assembly. ``last_probe_status`` is constrained to the app-level enum.

Revision ID: 0064_tenant_mcp_health
Revises: 0063_mcp_oauth_connection
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0064_tenant_mcp_health"
down_revision: str | Sequence[str] | None = "0063_mcp_oauth_connection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "tenant_mcp_server"
_STATUS_CHECK = "tenant_mcp_server_probe_status_check"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("last_probe_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(_TABLE, sa.Column("last_probe_status", sa.Text(), nullable=True))
    op.add_column(_TABLE, sa.Column("last_probe_error", sa.Text(), nullable=True))
    op.create_check_constraint(
        _STATUS_CHECK,
        _TABLE,
        "last_probe_status IN ('ok', 'error')",
    )


def downgrade() -> None:
    op.drop_constraint(_STATUS_CHECK, _TABLE, type_="check")
    op.drop_column(_TABLE, "last_probe_error")
    op.drop_column(_TABLE, "last_probe_status")
    op.drop_column(_TABLE, "last_probe_at")
