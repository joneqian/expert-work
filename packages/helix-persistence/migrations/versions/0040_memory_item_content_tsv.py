"""Capability Uplift Sprint #6 — memory_item.content_tsv.

Revision ID: 0040_memory_item_content_tsv
Revises: 0039_trigger_fire_scan_mode
Create Date: 2026-05-27

See ``docs/streams/STREAM-UPLIFT-DESIGN.md`` § 7 (Mini-ADR U-5 / U-6).

Adds the keyword side of hybrid memory retrieval (vector + full-text,
RRF-fused). The column is populated app-side by the ``SqlMemoryStore``
from jieba-segmented text under the ``simple`` config — Postgres'
built-in text-search configs do not segment CJK, so app-side
tokenization keeps keyword search correct for Chinese without a
``zhparser`` / ``pg_jieba`` extension. Mirrors the J.5
``knowledge_chunk.content_tsv`` pattern from migration 0022.

Schema deltas:

1. ``memory_item`` gains a nullable ``content_tsv`` ``tsvector`` column
   — existing rows backfill lazily on next write or update (a one-shot
   backfill migration is M1 if dogfood reveals stale rows hurting recall).
2. GIN index on ``content_tsv`` — backs the ``@@ plainto_tsquery``
   filter in :meth:`SqlMemoryStore.retrieve` hybrid path.

Metadata-only on Postgres ≥ 11 (ADD COLUMN nullable + CREATE INDEX
CONCURRENTLY would be live-safe; we use the standard non-concurrent
form because every M0 deployment already takes a brief lock window
on schema migration).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TSVECTOR

revision: str = "0040_memory_item_content_tsv"
down_revision: str | Sequence[str] | None = "0039_trigger_fire_scan_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "memory_item",
        sa.Column("content_tsv", TSVECTOR(), nullable=True),
    )
    op.create_index(
        "memory_item_content_tsv_idx",
        "memory_item",
        ["content_tsv"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("memory_item_content_tsv_idx", table_name="memory_item")
    op.drop_column("memory_item", "content_tsv")
