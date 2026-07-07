"""Stream K.K6 — memory_item.deleted_at for soft-delete + list ergonomics.

Revision ID: 0024_memory_soft_delete
Revises: 0023_api_key_rotation
Create Date: 2026-05-20

Stream K.K6 (Mini-ADR K-4): user-facing memory CRUD endpoints need a
soft-delete column so a forget action can be undone, and so a future
``retention-cleanup-job`` can hard-delete rows older than 30 days
without losing the audit trail in the meantime. ``retrieve`` /
``list_for_user`` filter out rows with ``deleted_at IS NOT NULL``.

Adds a partial index on ``(user_id, created_at DESC) WHERE deleted_at
IS NULL`` so the list endpoint's per-user listing stays cheap even
for users with many memories.

Schema change only — no policy / role changes. RLS policy from 0017
already keys on ``(tenant_id, user_id)``; soft-deleted rows still need
the same isolation.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_memory_soft_delete"
down_revision: str | Sequence[str] | None = "0023_api_key_rotation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "memory_item",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Partial index: only live rows are listed by the K6 GET endpoint,
    # and the planner / retrieve queries only ever scan live rows too.
    op.execute(
        "CREATE INDEX memory_item_live_user_idx ON memory_item "
        "(user_id, created_at DESC) WHERE deleted_at IS NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS memory_item_live_user_idx;")
    op.drop_column("memory_item", "deleted_at")
