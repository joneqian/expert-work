"""tenant_mcp_server custom HTTP headers + SSE read timeout (M1).

Lets tenants attach arbitrary HTTP headers (e.g. ``X-API-Key``) to a remote
MCP server and override the SSE read timeout. Header *values* may be secrets,
so the whole ``{name: value}`` map is stored as one encrypted SecretStore blob
and only its ``secret://`` ref (``custom_headers_ref``) lives on the row; the
non-secret header names (``custom_header_names``) are kept so the UI can list
configured headers without decrypting. ``sse_read_timeout_s`` NULL keeps the
SDK default. All three NULL on existing rows.

Revision ID: 0090_tenant_mcp_custom_headers
Revises: 0089_model_pricing_simplify
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0090_tenant_mcp_custom_headers"
down_revision: str | Sequence[str] | None = "0089_model_pricing_simplify"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "tenant_mcp_server"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("custom_headers_ref", sa.Text(), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column("custom_header_names", sa.ARRAY(sa.Text()), nullable=True),
    )
    op.add_column(_TABLE, sa.Column("sse_read_timeout_s", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, "sse_read_timeout_s")
    op.drop_column(_TABLE, "custom_header_names")
    op.drop_column(_TABLE, "custom_headers_ref")
