"""knowledge_document — durable ingestion + retained bytes.

Stream KB. Makes document ingestion durable and re-drivable: ``attempts``
bounds retries, ``claimed_at`` / ``lease_until`` let a reaper CAS-claim and
reclaim crashed/expired work, and ``content`` retains the original file bytes
so a failed/crashed/re-ingested document can be re-driven without re-upload
(``content_sha256`` for integrity/dedup). The partial index backs the reaper's
scan over non-terminal documents.

Revision ID: 0101_knowledge_ingest_durability
Revises: 0100_knowledge_base_uplift
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0101_knowledge_ingest_durability"
down_revision: str | Sequence[str] | None = "0100_knowledge_base_uplift"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "knowledge_document"
_RECOVERY_IDX = "knowledge_document_recovery_idx"


def upgrade() -> None:
    op.add_column(
        _TABLE, sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0"))
    )
    op.add_column(_TABLE, sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(_TABLE, sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column(_TABLE, sa.Column("content", sa.LargeBinary(), nullable=True))
    op.add_column(_TABLE, sa.Column("content_sha256", sa.Text(), nullable=True))
    op.create_index(
        _RECOVERY_IDX,
        _TABLE,
        ["lease_until"],
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )


def downgrade() -> None:
    op.drop_index(_RECOVERY_IDX, table_name=_TABLE)
    op.drop_column(_TABLE, "content_sha256")
    op.drop_column(_TABLE, "content")
    op.drop_column(_TABLE, "lease_until")
    op.drop_column(_TABLE, "claimed_at")
    op.drop_column(_TABLE, "attempts")
