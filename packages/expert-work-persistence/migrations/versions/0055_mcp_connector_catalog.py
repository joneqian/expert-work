"""mcp_connector_catalog platform-curated table + NULL-tenant RLS — Stream W (W-1).

Platform-curated catalog of MCP connector *types*. Mirrors the
``0050_encrypted_secret`` NULL-tenant pattern: ``tenant_id`` is NULLABLE
(NULL = platform-global, the only shape today; the column is kept so future
per-tenant private catalogs are a non-migration change). RLS uses an
``IS NOT DISTINCT FROM`` policy so future tenant rows are isolated while NULL
(platform) rows remain reachable under the bypass path.

The "one entry per name" invariant uses a unique index over
``COALESCE(tenant_id, <zero-uuid>)`` because Postgres treats NULLs as distinct —
a plain ``UNIQUE (tenant_id, name)`` would NOT collide two NULL-tenant rows
sharing a name.

Revision ID: 0055_mcp_connector_catalog
Revises: 0054_tenant_mcp_server
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0055_mcp_connector_catalog"
down_revision: str | Sequence[str] | None = "0054_tenant_mcp_server"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "mcp_connector_catalog"
_POLICY = "mcp_connector_catalog_tenant_isolation"
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("category", sa.Text(), nullable=False, server_default=sa.text("'general'")),
        sa.Column("icon", sa.Text(), nullable=True),
        sa.Column("transport", sa.Text(), nullable=False),
        sa.Column("url_template", sa.Text(), nullable=False),
        sa.Column("auth_type", sa.Text(), nullable=False, server_default=sa.text("'none'")),
        sa.Column("auth_schema", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("required_tier", sa.Text(), nullable=False, server_default=sa.text("'free'")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "transport IN ('sse', 'streamable_http')",
            name="mcp_connector_catalog_transport_check",
        ),
        sa.CheckConstraint(
            "auth_type IN ('none', 'bearer')",
            name="mcp_connector_catalog_auth_type_check",
        ),
        sa.CheckConstraint(
            "required_tier IN ('free', 'pro', 'enterprise')",
            name="mcp_connector_catalog_required_tier_check",
        ),
    )
    # One entry per name. COALESCE collapses NULL tenant_id to a sentinel so
    # platform (NULL) rows actually collide (NULLs are otherwise distinct in a
    # unique index).
    op.execute(
        f"CREATE UNIQUE INDEX mcp_connector_catalog_name_uniq ON {_TABLE} "
        f"(COALESCE(tenant_id, '{_ZERO_UUID}'::uuid), name)"
    )
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"CREATE POLICY {_POLICY} ON {_TABLE} "
        "USING (tenant_id IS NOT DISTINCT FROM "
        "NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id IS NOT DISTINCT FROM "
        "NULLIF(current_setting('app.tenant_id', true), '')::uuid);"
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE};")
    op.drop_table(_TABLE)
