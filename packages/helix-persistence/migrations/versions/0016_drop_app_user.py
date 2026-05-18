"""Drop the unused ``app_user`` placeholder table.

Revision ID: 0016_drop_app_user
Revises: 0015_tenant_user
Create Date: 2026-05-18

``app_user`` was created in migration 0004 as a placeholder for an
end-user identity table — local password accounts plus OIDC-federated
users (subsystems/15 § 3.1). It was never built out: no ORM model, no
store, no login endpoint; no code ever writes a row.

Stream J.14's ``tenant_user`` registry now owns the per-user identity
concept (resolved from the authenticated ``Principal``). ``app_user``'s
only distinct feature — local password authentication
(``password_hash`` / ``failed_logins`` / ``locked_until``) — is not on
the roadmap: an IdP-federated enterprise platform delegates identity to
the tenant's OIDC provider rather than keeping its own password store.

Contract-phase migration: the table is empty and has no foreign keys,
so the drop is safe. ``downgrade`` recreates the 0004 schema verbatim
for reversibility.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_drop_app_user"
down_revision: str | Sequence[str] | None = "0015_tenant_user"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.drop_table("app_user")


def downgrade() -> None:
    # Recreate the table exactly as migration 0004 shipped it.
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
