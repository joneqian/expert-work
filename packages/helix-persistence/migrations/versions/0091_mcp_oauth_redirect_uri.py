"""mcp_oauth_connection redirect_uri (multi-client OAuth).

Persists the per-initiate redirect URI a client supplied so the callback reuses
the exact same value for the token exchange (OAuth 2.1 requires authorize and
token-exchange ``redirect_uri`` to match). NULL = the connection used the global
``mcp_oauth_redirect_uri`` default.

Revision ID: 0091_mcp_oauth_redirect_uri
Revises: 0090_tenant_mcp_custom_headers
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0091_mcp_oauth_redirect_uri"
down_revision: str | Sequence[str] | None = "0090_tenant_mcp_custom_headers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "mcp_oauth_connection"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("redirect_uri", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, "redirect_uri")
