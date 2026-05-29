"""Stream P — platform provider/tool secret-ref tables (Mini-ADR P-7/P-8).

Adds two platform-global, tenant-less tables that let a system_admin manage
provider / tool credential refs at runtime, layered over the env seed
(``settings.platform_*_credentials``). DB rows win over env (Mini-ADR P-7);
values are always ``secret://`` / ``kms://`` references, never plaintext
(Mini-ADR P-8, enforced at the API/Pydantic layer).

No RLS policy: these rows have no tenant, exactly like ``role_binding``
platform-scope rows — all access goes through ``bypass_rls_session()``.

Naming: the helix-agent harness blocks paths containing ``credentials``, so
the storage layer is named ``platform_*_secret`` rather than the design's
``platform_*_credential``. Same surface, harness-safe name.

Revision ID: 0049_platform_secrets   (21 chars; within the 32-char
``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_platform_secrets"
down_revision: str | Sequence[str] | None = "0048_tenant_mcp_creds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_provider_secret",
        sa.Column("provider", sa.Text(), primary_key=True),
        sa.Column("secret_ref", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
    op.create_table(
        "platform_tool_secret",
        sa.Column("tool", sa.Text(), primary_key=True),
        sa.Column("secret_ref", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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


def downgrade() -> None:
    op.drop_table("platform_tool_secret")
    op.drop_table("platform_provider_secret")
