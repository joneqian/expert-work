"""Stream C.7 ``tenant_config`` table — per-tenant runtime configuration.

Revision ID: 0007_tenant_config
Revises: 0006_quota_tables
Create Date: 2026-05-13

Lands the last C-stream table (STREAM-C-DESIGN § 2.4 / § 2.8): one
row per tenant holding the configuration the orchestrator + LLM
gateway will consume in Stream E.

Columns (all defaulted so an admin can hot-patch one field at a time
without re-supplying the whole row):

* ``display_name``           — human-readable label.
* ``plan``                   — pricing tier (``free`` / ``pro`` /
  ``enterprise``); informational only in M0.
* ``model_credentials_ref``  — JSONB ``{<provider>: "kms://..."}``;
  Stream F.6 resolves the URI to the real secret at LLM-call time.
* ``mcp_allowlist``          — JSONB array of MCP-server names this
  tenant is allowed to use.
* ``rate_limit_override``    — JSONB tuned values that override the
  defaults in :class:`TenantRateLimitMiddleware` (M1 wires this).
* ``pii_fields``              — JSONB array of field paths the
  Stream D PII redactor will scrub.
* ``updated_by``             — actor_id for audit attribution.

RLS uses the canonical NULLIF policy from C.4 so unset GUC sessions
fail closed without raising ``InvalidTextRepresentationError``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_tenant_config"
down_revision: str | Sequence[str] | None = "0006_quota_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "tenant_config",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("plan", sa.Text(), nullable=False, server_default=sa.text("'free'")),
        sa.Column(
            "model_credentials_ref",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "mcp_allowlist",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "rate_limit_override",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "pii_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.Text(), nullable=False),
    )

    op.execute("ALTER TABLE tenant_config ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE tenant_config FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_config_self ON tenant_config;")
    op.execute(
        """
        CREATE POLICY tenant_config_self ON tenant_config
            USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_config_self ON tenant_config;")
    op.execute("ALTER TABLE tenant_config NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE tenant_config DISABLE ROW LEVEL SECURITY;")
    op.drop_table("tenant_config")
