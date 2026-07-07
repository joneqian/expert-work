"""knowledge_base — commercial uplift columns.

Stream KB (management surface + retrieval transparency). Adds to
``knowledge_base``: free-text ``description`` + ``created_by`` + ``updated_at``;
per-base retrieval defaults (``retrieval_top_k`` / ``retrieval_score_threshold``
/ ``retrieval_method`` / ``rerank_enabled``) so retrieval is configurable not
hardcoded; and the embedding-model pin (``embedding_provider`` /
``embedding_model`` + ``reindex_requested_at``) so a model swap is detectable
and re-indexable.

Document-level durability columns land separately in ``0101``.

Revision ID: 0100_knowledge_base_uplift
Revises: 0099_memory_item_scores
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0100_knowledge_base_uplift"
down_revision: str | Sequence[str] | None = "0099_memory_item_scores"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "knowledge_base"
_METHOD_CHECK = "knowledge_base_retrieval_method_check"
_TOPK_CHECK = "knowledge_base_retrieval_top_k_check"
_THRESHOLD_CHECK = "knowledge_base_retrieval_threshold_check"


def upgrade() -> None:
    # Metadata.
    op.add_column(_TABLE, sa.Column("description", sa.Text(), nullable=True))
    op.add_column(_TABLE, sa.Column("created_by", sa.Text(), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Per-base retrieval defaults.
    op.add_column(
        _TABLE,
        sa.Column("retrieval_top_k", sa.Integer(), nullable=False, server_default=sa.text("5")),
    )
    op.add_column(_TABLE, sa.Column("retrieval_score_threshold", sa.Float(), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column(
            "retrieval_method",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'hybrid'"),
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column("rerank_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    # Embedding-model pin.
    op.add_column(_TABLE, sa.Column("embedding_provider", sa.Text(), nullable=True))
    op.add_column(_TABLE, sa.Column("embedding_model", sa.Text(), nullable=True))
    op.add_column(
        _TABLE, sa.Column("reindex_requested_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_check_constraint(
        _METHOD_CHECK, _TABLE, "retrieval_method IN ('vector', 'keyword', 'hybrid')"
    )
    op.create_check_constraint(_TOPK_CHECK, _TABLE, "retrieval_top_k BETWEEN 1 AND 50")
    op.create_check_constraint(
        _THRESHOLD_CHECK,
        _TABLE,
        "retrieval_score_threshold IS NULL OR retrieval_score_threshold BETWEEN 0 AND 1",
    )


def downgrade() -> None:
    op.drop_constraint(_THRESHOLD_CHECK, _TABLE, type_="check")
    op.drop_constraint(_TOPK_CHECK, _TABLE, type_="check")
    op.drop_constraint(_METHOD_CHECK, _TABLE, type_="check")
    op.drop_column(_TABLE, "reindex_requested_at")
    op.drop_column(_TABLE, "embedding_model")
    op.drop_column(_TABLE, "embedding_provider")
    op.drop_column(_TABLE, "rerank_enabled")
    op.drop_column(_TABLE, "retrieval_method")
    op.drop_column(_TABLE, "retrieval_score_threshold")
    op.drop_column(_TABLE, "retrieval_top_k")
    op.drop_column(_TABLE, "updated_at")
    op.drop_column(_TABLE, "created_by")
    op.drop_column(_TABLE, "description")
