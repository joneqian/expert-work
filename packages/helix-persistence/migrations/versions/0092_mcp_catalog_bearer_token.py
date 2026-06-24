"""mcp_connector_catalog bearer_token_ref (platform-configured shared server).

Stream MCP platform-servers (P1). A platform admin can now configure a fully
usable shared-bearer MCP server in the catalog: the platform supplies the bearer
token once, it is stored in the SecretStore, and this column holds only the
``secret://`` ref. Tenants select/enable the server (shared identity) rather than
filling their own credentials. NULL = no platform bearer (none/oauth2 entries, or
a legacy tenant-fills bearer that still carries an ``auth_schema`` secret field).

Revision ID: 0092_mcp_catalog_bearer_token
Revises: 0091_mcp_oauth_redirect_uri
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0092_mcp_catalog_bearer_token"
down_revision: str | Sequence[str] | None = "0091_mcp_oauth_redirect_uri"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "mcp_connector_catalog"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("bearer_token_ref", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, "bearer_token_ref")
