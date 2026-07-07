"""Stream C.3 authn / authz baseline tables.

Revision ID: 0004_authn_authz_tables
Revises: 0003_agent_spec
Create Date: 2026-05-13

Adds the five tables enumerated in STREAM-C-DESIGN § 2.4 / subsystems/15
§ 3.1:

* ``app_user`` — local + OIDC-federated end users (no endpoints yet; the
  schema lands now so C.7 / RLS migrations don't have to revisit the
  table layout).
* ``service_account`` — tenant-scoped programmatic identities.
* ``api_key`` — bearer credentials. ``prefix`` is the recognisable
  ``aforge_pat_<5hex>_`` segment (unique index for O(1) lookup);
  ``secret_hash`` is the argon2id digest of the full bytes.
* ``role_binding`` — ``(subject, tenant, role)`` triples.
* ``jwt_blacklist`` — opaque ``jti`` parking lot for revoked tokens
  (enforcement lands later — schema-only today).

The C.4 PR (next in Stream C) will turn ROW LEVEL SECURITY on for the
three tenant-scoped tables here.

Note on migration numbering: STREAM-C-DESIGN § 2.4 sketched 0004 = RLS
baseline and 0005 = auth tables, in design order. We are landing C.3
before C.4 in implementation order, so the actual alembic numbers swap:
0004 = auth tables (this PR), 0005 = RLS baseline (next PR).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_authn_authz_tables"
down_revision: str | Sequence[str] | None = "0003_agent_spec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("username", sa.Text(), nullable=False, unique=True),
        sa.Column("email", sa.Text(), nullable=True, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("oidc_issuer", sa.Text(), nullable=True),
        sa.Column("oidc_subject", sa.Text(), nullable=True),
        sa.Column("default_tenant", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("failed_logins", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("oidc_issuer", "oidc_subject", name="app_user_oidc_uniq"),
    )

    op.create_table(
        "service_account",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", "name", name="service_account_tenant_name_uniq"),
    )

    op.create_table(
        "api_key",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "service_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False, unique=True),
        sa.Column("secret_hash", sa.Text(), nullable=False),
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("api_key_tenant_idx", "api_key", ["tenant_id"])
    op.create_index("api_key_service_account_idx", "api_key", ["service_account_id"])

    op.create_table(
        "role_binding",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("subject_type", sa.Text(), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("granted_by", sa.Text(), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "subject_type",
            "subject_id",
            "tenant_id",
            "role",
            name="role_binding_subject_tenant_role_uniq",
        ),
    )
    op.create_index("role_binding_subject_idx", "role_binding", ["subject_type", "subject_id"])
    op.create_index("role_binding_tenant_idx", "role_binding", ["tenant_id"])

    op.create_table(
        "jwt_blacklist",
        sa.Column("jti", sa.Text(), primary_key=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("jwt_blacklist")
    op.drop_index("role_binding_tenant_idx", table_name="role_binding")
    op.drop_index("role_binding_subject_idx", table_name="role_binding")
    op.drop_table("role_binding")
    op.drop_index("api_key_service_account_idx", table_name="api_key")
    op.drop_index("api_key_tenant_idx", table_name="api_key")
    op.drop_table("api_key")
    op.drop_table("service_account")
    op.drop_table("app_user")
