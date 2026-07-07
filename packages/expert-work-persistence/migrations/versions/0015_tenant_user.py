"""Stream J.14 — per-user scope: tenant_user registry + thread_meta.user_id.

Revision ID: 0015_tenant_user
Revises: 0014_feedback
Create Date: 2026-05-18

Makes "user" a first-class entity (STREAM-J-DESIGN § 4). A *user* is a
principal — human or service account — acting within a tenant; the
canonical product form gives each user a persistent agent instance.

* ``tenant_user`` — the per-user registry. Tenant-scoped, RLS-enabled
  with the canonical tenant-isolation policy (identical to migration
  0005). ``(tenant_id, subject_type, subject_id)`` is the identity key;
  ``id`` is the surrogate ``user_id`` owned tables reference.

* ``thread_meta.user_id`` — a nullable column recording which user owns
  the thread. **No foreign key**: the codebase keeps cross-table links
  FK-light (cf. ``feedback.thread_id``), and ``tenant_user`` is
  ``FORCE`` RLS — an FK's referential-integrity check against a
  FORCE-RLS table is a known footgun. Integrity is maintained at the
  store / application layer. Nullable so threads created before J.14
  (and machine-triggered threads with no user) keep working.

Hard isolation stays at the tenant boundary; ``user_id`` is an
application-layer ownership scope (Mini-ADR J-1) — no user-level RLS
GUC lands here. The first user-level RLS policy arrives with J.3's
long-term-memory table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0015_tenant_user"
down_revision: str | Sequence[str] | None = "0014_feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_POLICY = "tenant_user_tenant_isolation"


def upgrade() -> None:
    op.create_table(
        "tenant_user",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("subject_type", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "subject_type",
            "subject_id",
            name="tenant_user_identity_uniq",
        ),
    )
    op.create_index("tenant_user_tenant_idx", "tenant_user", ["tenant_id"])

    # Row-level security — the canonical tenant-isolation policy (0005).
    op.execute("ALTER TABLE tenant_user ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE tenant_user FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON tenant_user;")
    op.execute(
        f"""
        CREATE POLICY {_POLICY} ON tenant_user
            USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        """
    )

    # thread_meta gains user ownership — nullable, no FK (see module docstring).
    op.add_column("thread_meta", sa.Column("user_id", UUID(as_uuid=True), nullable=True))
    op.create_index("thread_meta_tenant_user_idx", "thread_meta", ["tenant_id", "user_id"])


def downgrade() -> None:
    op.drop_index("thread_meta_tenant_user_idx", table_name="thread_meta")
    op.drop_column("thread_meta", "user_id")

    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON tenant_user;")
    op.drop_index("tenant_user_tenant_idx", table_name="tenant_user")
    op.drop_table("tenant_user")
