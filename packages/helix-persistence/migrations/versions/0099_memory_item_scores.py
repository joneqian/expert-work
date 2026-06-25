"""memory_item.importance / confidence — write-filter scoring.

Stream Memory-Enhance (M-2). Adds two 0-1 scores to each memory:
``importance`` (future-reuse value, feeds the writeback write-filter) and
``confidence`` (extraction certainty; 1.0 = user-asserted via the M-4
correction API). Both default 0.5 (neutral) so backfilled legacy rows carry no
bias. A CHECK keeps both in [0, 1].

Revision ID: 0099_memory_item_scores
Revises: 0098_memory_item_agent_name
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0099_memory_item_scores"
down_revision: str | Sequence[str] | None = "0098_memory_item_agent_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "memory_item"
_CHECK = "memory_item_score_check"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("importance", sa.Float(), nullable=False, server_default=sa.text("0.5")),
    )
    op.add_column(
        _TABLE,
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.5")),
    )
    op.create_check_constraint(
        _CHECK,
        _TABLE,
        "importance BETWEEN 0 AND 1 AND confidence BETWEEN 0 AND 1",
    )


def downgrade() -> None:
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.drop_column(_TABLE, "confidence")
    op.drop_column(_TABLE, "importance")
