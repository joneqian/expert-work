"""Capability Uplift Sprint #7 — memory_item consolidator columns.

Revision ID: 0045_memory_consolidator
Revises: 0044_tenant_curator_days
Create Date: 2026-05-28

Adds 4 columns to ``memory_item`` + 2 partial indexes for the
MemoryConsolidator worker (see ``docs/streams/STREAM-UPLIFT-DESIGN.md``
§ 8, Mini-ADR U-33):

* ``status`` (VARCHAR(16), default ``'transient'``) — lifecycle state.
  ``transient`` (raw writeback default), ``consolidated`` (worker output),
  ``archived`` (M2-C reserved). Backfilled to ``'transient'`` for all
  existing rows so the writeback path remains unaffected.

* ``consolidated_into`` (UUID, nullable) — set on transient items that
  have been superseded by a consolidated parent. ``MemoryStore.retrieve()``
  default WHERE excludes such items to prevent double-counting raw + summary.

* ``consolidated_from`` (JSONB, default ``'[]'::jsonb``) — only populated
  on consolidated items. Reverse index of source transient UUIDs.

* ``last_reviewed_at`` (TIMESTAMPTZ, nullable) — set by the lone-item
  noise-purge sub-pass when LLM classifies an aged lone transient as
  durable. NULL ↔ never reviewed (re-eligible); non-NULL ↔ skip
  re-review (prevents borderline-fact LLM thrash).

Two partial indexes:

* ``ix_memory_item_consolidator_scan`` — covers the SUB-PASS scan
  ``WHERE status = 'transient' AND deleted_at IS NULL``. Includes
  ``created_at`` for the purge-candidate ORDER BY.

* ``ix_memory_item_consolidated_into`` — covers retrieve() filter +
  potential future reverse lookup ``WHERE consolidated_into = ?``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0045_memory_consolidator"
down_revision: str | Sequence[str] | None = "0044_tenant_curator_days"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "memory_item",
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'transient'"),
        ),
    )
    op.add_column(
        "memory_item",
        sa.Column(
            "consolidated_into",
            PG_UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "memory_item",
        sa.Column(
            "consolidated_from",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "memory_item",
        sa.Column(
            "last_reviewed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Guard the status whitelist. Mirrors the Python ``MemoryStatus``
    # Literal alias in expert_work.protocol.memory_item.
    op.create_check_constraint(
        "memory_item_status_check",
        "memory_item",
        "status IN ('transient', 'consolidated', 'archived')",
    )

    # SUB-PASS scan path (per tenant + user). status='transient' filter
    # is the partial-index predicate; deleted_at NULL keeps soft-deleted
    # rows out cheaply. created_at trailing column supports the purge
    # candidate ORDER BY (oldest first).
    op.create_index(
        "ix_memory_item_consolidator_scan",
        "memory_item",
        ["tenant_id", "user_id", "created_at"],
        postgresql_where=sa.text("status = 'transient' AND deleted_at IS NULL"),
    )

    # Reverse-lookup index for "list all items consolidated into X" +
    # accelerates the retrieve() default-WHERE NULL check on hot path.
    op.create_index(
        "ix_memory_item_consolidated_into",
        "memory_item",
        ["consolidated_into"],
        postgresql_where=sa.text("consolidated_into IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_memory_item_consolidated_into", table_name="memory_item")
    op.drop_index("ix_memory_item_consolidator_scan", table_name="memory_item")
    op.drop_constraint("memory_item_status_check", "memory_item", type_="check")
    op.drop_column("memory_item", "last_reviewed_at")
    op.drop_column("memory_item", "consolidated_from")
    op.drop_column("memory_item", "consolidated_into")
    op.drop_column("memory_item", "status")
