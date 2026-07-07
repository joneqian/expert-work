"""Stream J.15 — per-user execution environment: user_workspace table.

Revision ID: 0018_user_workspace
Revises: 0017_long_term_memory
Create Date: 2026-05-19

The durable workspace of the per-user persistent agent (STREAM-J-DESIGN
§ 9). Adds ``user_workspace`` — one row per ``(tenant_id, user_id)``
pair, registering the docker named volume that backs that user's
``/workspace``. The volume outlives the ephemeral sandbox containers
that mount it ("临时容器 + 持久卷", Mini-ADR J-9).

* ``user_workspace`` carries **no RLS** — it is owned by the
  sandbox-supervisor, the same system service that owns
  ``sandbox_instance`` (also un-RLS'd, migration ``0012``). The
  supervisor authenticates callers via mTLS and scopes by
  ``(tenant_id, user_id)`` in the application layer; its DB sessions do
  not set the ``app.*`` GUCs, so an RLS predicate would fail every
  query (Mini-ADR J-1 — ``user_workspace`` is the documented exception).

* ``sandbox_instance`` gains ``user_id`` / ``workspace_id`` — both
  nullable: a sandbox acquired without a user (the pre-J.15 tmpfs path)
  leaves them NULL, so this migration is backward-compatible.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0018_user_workspace"
down_revision: str | Sequence[str] | None = "0017_long_term_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "user_workspace",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("volume_name", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_accessed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # One workspace per (tenant, user) — the resolve() upsert key.
        # The constraint's index also serves the (tenant_id, user_id)
        # lookup, so no separate index is created.
        sa.UniqueConstraint("tenant_id", "user_id", name="user_workspace_identity_uniq"),
    )

    # Link sandbox containers to their per-user workspace. Nullable —
    # the pre-J.15 ephemeral-tmpfs path leaves both NULL.
    op.add_column(
        "sandbox_instance",
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "sandbox_instance",
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sandbox_instance", "workspace_id")
    op.drop_column("sandbox_instance", "user_id")
    op.drop_table("user_workspace")
