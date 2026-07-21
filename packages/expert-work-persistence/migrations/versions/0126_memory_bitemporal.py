"""P5b — memory_item 溯源 + bi-temporal 列.

Revision ID: 0126_memory_bitemporal
Revises: 0125_memory_access_count
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0126_memory_bitemporal"
down_revision = "0125_memory_access_count"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("memory_item", sa.Column("source_run_id", sa.Text(), nullable=True))
    op.add_column("memory_item", sa.Column("valid_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("memory_item", sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("memory_item", sa.Column("invalid_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "memory_item",
        sa.Column("supersedes", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "memory_item",
        sa.Column("superseded_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("memory_item", sa.Column("expected_valid_days", sa.Integer(), nullable=True))
    # Backfill valid_at = created_at so existing rows anchor world-validity at
    # creation (harmless for default retrieval; used by as_of time-travel in P5b-2).
    op.execute("UPDATE memory_item SET valid_at = created_at WHERE valid_at IS NULL")


def downgrade() -> None:
    op.drop_column("memory_item", "expected_valid_days")
    op.drop_column("memory_item", "superseded_by")
    op.drop_column("memory_item", "supersedes")
    op.drop_column("memory_item", "invalid_at")
    op.drop_column("memory_item", "expired_at")
    op.drop_column("memory_item", "valid_at")
    op.drop_column("memory_item", "source_run_id")
