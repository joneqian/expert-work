"""platform_agent_template platform-curated table + NULL-tenant RLS.

Stream Agent-Templates (M1). Platform-curated catalog of official Agent
*templates* (full base AgentSpec manifests). Mirrors ``0055_mcp_connector_catalog``:
``tenant_id`` is NULLABLE (NULL = platform-global, the only shape today; the column
is kept so future per-tenant private template libraries are a non-migration
change). RLS uses an ``IS NOT DISTINCT FROM`` policy so future tenant rows are
isolated while NULL (platform) rows remain reachable under the bypass path.

Versioned by ``(name, version)`` like ``agent_spec`` so a tenant can pin
``extends: name@1.2.0``. The uniqueness uses ``COALESCE(tenant_id, <zero-uuid>)``
so platform (NULL) rows actually collide (NULLs are otherwise distinct in a unique
index).

Revision ID: 0095_platform_agent_template
Revises: 0094_mcp_catalog_disabled_tools
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import CHAR, JSONB, UUID

revision: str = "0095_platform_agent_template"
down_revision: str | Sequence[str] | None = "0094_mcp_catalog_disabled_tools"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "platform_agent_template"
_POLICY = "platform_agent_template_tenant_isolation"
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
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("spec_json", JSONB(), nullable=False),
        sa.Column("spec_sha256", CHAR(64), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("category", sa.Text(), nullable=False, server_default=sa.text("'general'")),
        sa.Column("icon", sa.Text(), nullable=True),
        sa.Column("required_tier", sa.Text(), nullable=False, server_default=sa.text("'free'")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.Text(), nullable=False),
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
        sa.CheckConstraint(
            "required_tier IN ('free', 'pro', 'enterprise')",
            name="platform_agent_template_required_tier_check",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published')",
            name="platform_agent_template_status_check",
        ),
    )
    # One row per (name, version). COALESCE collapses NULL tenant_id to a sentinel
    # so platform (NULL) rows actually collide (NULLs are otherwise distinct).
    op.execute(
        f"CREATE UNIQUE INDEX platform_agent_template_name_version_uniq ON {_TABLE} "
        f"(COALESCE(tenant_id, '{_ZERO_UUID}'::uuid), name, version)"
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
