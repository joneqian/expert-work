"""mcp_connector_catalog disabled_tools (platform-curated per-tool denylist).

Stream MCP platform-servers (form polish). A platform admin can disable specific
tools a server advertises; the names live here and are filtered from list_tools
at runtime so no tenant agent ever sees them. Empty (default) = every advertised
tool is exposed.

Revision ID: 0094_mcp_catalog_disabled_tools
Revises: 0093_mcp_catalog_timeouts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0094_mcp_catalog_disabled_tools"
down_revision: str | Sequence[str] | None = "0093_mcp_catalog_timeouts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "mcp_connector_catalog"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "disabled_tools",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column(_TABLE, "disabled_tools")
