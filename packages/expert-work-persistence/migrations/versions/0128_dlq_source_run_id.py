"""P5b — memory_writeback_dlq.source_run_id (DLQ 溯源补齐).

Revision ID: 0128_dlq_source_run_id
Revises: 0127_memory_dedup_bitemporal
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0128_dlq_source_run_id"
down_revision = "0127_memory_dedup_bitemporal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("memory_writeback_dlq", sa.Column("source_run_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("memory_writeback_dlq", "source_run_id")
