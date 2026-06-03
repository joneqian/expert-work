"""Additive catalog columns on tenant_mcp_server + tenant_config — Stream W (W-2/W-4).

Two purely-additive ALTERs, no backfill:

* ``tenant_mcp_server.catalog_id`` UUID NULL → FK to ``mcp_connector_catalog(id)``
  ``ON DELETE RESTRICT``. NULL = off-catalog custom row (every Stream V row stays
  NULL = a valid custom instance — zero behavior change); non-NULL = a catalog
  instance.
* ``tenant_config.allow_custom_mcp_servers`` Boolean NOT NULL default ``true``
  (Mini-ADR W-4 kill-switch; default preserves Stream V self-service).

Revision ID: 0056_mcp_catalog_columns
Revises: 0055_mcp_connector_catalog
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0056_mcp_catalog_columns"
down_revision: str | Sequence[str] | None = "0055_mcp_connector_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_FK = "tenant_mcp_server_catalog_id_fkey"


def upgrade() -> None:
    op.add_column(
        "tenant_mcp_server",
        sa.Column("catalog_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        _FK,
        "tenant_mcp_server",
        "mcp_connector_catalog",
        ["catalog_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "tenant_config",
        sa.Column(
            "allow_custom_mcp_servers",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_config", "allow_custom_mcp_servers")
    op.drop_constraint(_FK, "tenant_mcp_server", type_="foreignkey")
    op.drop_column("tenant_mcp_server", "catalog_id")
