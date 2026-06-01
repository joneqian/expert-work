"""Stream R — tenant_member onboarding roster (Mini-ADR R-3/R-6/R-10).

The invitation-state roster of a tenant: who an admin invited, their role, and
their ``invited → active → suspended / revoked`` lifecycle. Control-plane
source of truth for membership, distinct from ``tenant_user`` (runtime JIT
registry); connected by ``keycloak_user_id`` with no FK (a FORCE-RLS table FK
is a known footgun — see 0015).

The partial-unique index over ``(tenant_id, lower(email)) WHERE status !=
'revoked'`` enforces one active invite per email while letting a revoked email
be re-invited (Mini-ADR R-10). RLS uses the canonical tenant-isolation policy;
W1 build-tenant / W3 first-login writes happen under ``bypass_rls_session()``.

Revision ID: 0051_tenant_member   (18 chars; within the 32-char ``version_num``
ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0051_tenant_member"
down_revision: str | Sequence[str] | None = "0050_encrypted_secret"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "tenant_member"
_POLICY = "tenant_member_tenant_isolation"


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
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("keycloak_user_id", sa.Text(), nullable=True),
        sa.Column("subject_id", UUID(as_uuid=True), nullable=True),
        sa.Column("invited_by", sa.Text(), nullable=False),
        sa.Column(
            "invited_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('invited', 'active', 'suspended', 'revoked')",
            name="tenant_member_status_check",
        ),
        sa.CheckConstraint(
            "status != 'active' OR (keycloak_user_id IS NOT NULL AND activated_at IS NOT NULL)",
            name="tenant_member_active_consistency",
        ),
    )
    op.create_index("tenant_member_tenant_idx", _TABLE, ["tenant_id"])
    # One active invite per (tenant, lower(email)); revoked rows excluded so a
    # revoked email can be re-invited (Mini-ADR R-10).
    op.execute(
        f"CREATE UNIQUE INDEX tenant_member_active_email_uniq ON {_TABLE} "
        "(tenant_id, lower(email)) WHERE status != 'revoked'"
    )
    # First-login reverse lookup (W3) by Keycloak user id.
    op.execute(
        f"CREATE INDEX tenant_member_kc_user_idx ON {_TABLE} "
        "(keycloak_user_id) WHERE keycloak_user_id IS NOT NULL"
    )
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
