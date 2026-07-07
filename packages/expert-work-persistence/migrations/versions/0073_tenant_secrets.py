"""Per-tenant provider/tool secret-ref override tables — Stream HX-8 (Mini-ADR HX-H1).

Sister tables to ``platform_provider_secret`` / ``platform_tool_secret``
(0049): a row here means "this tenant resolves this provider/tool through
its own platform-managed ref" (row present = override; ``enabled=false``
suppresses the key for the tenant entirely, mirroring the platform-row
P-12 semantics — Mini-ADR HX-H2). Still platform-procured and
system_admin-written: Stream Y-1's BYOK removal stands — tenants cannot
see or self-serve these rows.

RLS: ENABLE + tenant-equality policy as defence in depth (a stray
tenant-context read sees only its own rows; rows hold refs, never key
values). The platform service layer reads/writes via
``bypass_rls_session()`` like the 0049 tables.

Revision ID: 0073_tenant_secrets   (19 chars; within the 32-char
``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0073_tenant_secrets"
down_revision: str | Sequence[str] | None = "0072_agent_spec_revision"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def _create_override_table(table: str, key_column: str) -> None:
    op.create_table(
        table,
        sa.Column("tenant_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(key_column, sa.Text(), primary_key=True),
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
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def upgrade() -> None:
    _create_override_table("tenant_provider_secret", "provider")
    _create_override_table("tenant_tool_secret", "tool")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_tool_secret_tenant_isolation ON tenant_tool_secret")
    op.execute(
        "DROP POLICY IF EXISTS tenant_provider_secret_tenant_isolation ON tenant_provider_secret"
    )
    op.drop_table("tenant_tool_secret")
    op.drop_table("tenant_provider_secret")
