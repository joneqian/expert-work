"""mcp_connector_catalog OAuth columns + auth_type check widening — Stream MCP-OAUTH (OA-1a).

Adds the platform-registered OAuth-app metadata (``oauth_client_id`` /
``oauth_scopes``) used by ``auth_type='oauth2'`` catalog entries, and widens the
``auth_type`` CHECK to include ``'oauth2'``. No new table here — the per-user
``mcp_oauth_connection`` table lands in OA-1b.

Revision ID: 0062_mcp_catalog_oauth
Revises: 0061_ledger_audit_reader_grant
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0062_mcp_catalog_oauth"
down_revision: str | Sequence[str] | None = "0061_ledger_audit_reader_grant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "mcp_connector_catalog"
_CHECK = "mcp_connector_catalog_auth_type_check"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("oauth_client_id", sa.Text(), nullable=True))
    op.add_column(_TABLE, sa.Column("oauth_scopes", sa.Text(), nullable=True))
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(
        _CHECK,
        _TABLE,
        "auth_type IN ('none', 'bearer', 'oauth2')",
    )


def downgrade() -> None:
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(
        _CHECK,
        _TABLE,
        "auth_type IN ('none', 'bearer')",
    )
    op.drop_column(_TABLE, "oauth_scopes")
    op.drop_column(_TABLE, "oauth_client_id")
