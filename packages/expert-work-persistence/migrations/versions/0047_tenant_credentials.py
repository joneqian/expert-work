"""Stream O — per-tenant credentials mode + tool credentials.

Revision ID: 0047_tenant_credentials
Revises: 0046_memory_consolid_cfg
Create Date: 2026-05-28

Adds 2 columns to ``tenant_config`` for the Stream O credentials
management (Mini-ADR O-2):

* ``credentials_mode`` (VARCHAR(16), default ``'platform'`` CHECK
  ``IN ('platform', 'tenant')``) — controls whether LLM provider + tool
  API key lookups resolve via the platform secret_refs or the tenant's
  own. Default ``'platform'`` so all existing rows keep current behavior
  (resolve against ``platform_provider_credentials`` /
  ``platform_tool_credentials`` env config).

* ``tool_credentials`` (JSONB, default ``'{}'::jsonb``) — per-tenant
  tool API key map (``{tool_name: secret_ref}``). Counterpart to the
  pre-existing ``model_credentials_ref`` which carries the provider
  key map (kept under its legacy name to avoid a wide rename churn —
  see the Stream O PR B description for the trade-off).

Note: the migration **does not rename** ``model_credentials_ref`` even
though the design doc proposed it. The rename would touch ~7 files
including the admin UI + storybook + tests; the cost outweighs the
clarity win (the field's semantic already matches its name — it carries
LLM provider credentials).

Revision id ``0047_tenant_credentials`` = 23 chars (within the 32-char
alembic ``version_num`` ceiling per
[memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0047_tenant_credentials"
down_revision: str | Sequence[str] | None = "0046_memory_consolid_cfg"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "tenant_config",
        sa.Column(
            "credentials_mode",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'platform'"),
        ),
    )
    op.add_column(
        "tenant_config",
        sa.Column(
            "tool_credentials",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "tenant_config_credentials_mode_ck",
        "tenant_config",
        "credentials_mode IN ('platform', 'tenant')",
    )


def downgrade() -> None:
    op.drop_constraint("tenant_config_credentials_mode_ck", "tenant_config", type_="check")
    op.drop_column("tenant_config", "tool_credentials")
    op.drop_column("tenant_config", "credentials_mode")
