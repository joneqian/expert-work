"""tenant_skill_subscription table + RLS — Skill Marketplace Phase 1.

A tenant's "I selected this platform skill" marker. Pure accounting/UX: it
does NOT gate the runtime fallback (the tier check on the resolver hot path is
the real gate — semantic A in docs/design/skill-marketplace-ia.md). No skill
content is copied (skills are pure content, unlike MCP instances which carry
per-tenant secrets).

Revision ID: 0086_tenant_skill_subscription
Revises: 0085_tenant_member_audit_grant
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0086_tenant_skill_subscription"
down_revision: str | Sequence[str] | None = "0085_tenant_member_audit_grant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "tenant_skill_subscription"
_POLICY = "tenant_skill_subscription_tenant_isolation"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        # No FK: the platform skill is a NULL-tenant row (cross-RLS) and the
        # subscription is a soft association — a deleted/archived platform skill
        # leaves a dangling row, harmless because the merged view only joins
        # ACTIVE platform skills.
        sa.Column("platform_skill_id", UUID(as_uuid=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by", sa.Text(), nullable=False),
    )
    op.create_index("tenant_skill_subscription_tenant_idx", _TABLE, ["tenant_id"])
    op.create_index(
        "tenant_skill_subscription_uniq",
        _TABLE,
        ["tenant_id", "platform_skill_id"],
        unique=True,
    )

    # Standard tenant-scoped RLS. FORCE is safe here: the subscription table has
    # no cross-tenant scan need (unlike the skill table 0057, which deliberately
    # omits FORCE so the owner-role curator sweep can read every tenant).
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"CREATE POLICY {_POLICY} ON {_TABLE} "
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);"
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE};")
    op.drop_table(_TABLE)
