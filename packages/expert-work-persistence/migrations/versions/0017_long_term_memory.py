"""Stream J.3 — long-term memory: pgvector + memory_item table.

Revision ID: 0017_long_term_memory
Revises: 0016_drop_app_user
Create Date: 2026-05-18

Cross-session memory for the per-user persistent agent (STREAM-J-DESIGN
§ 8). Enables the ``vector`` extension and adds ``memory_item`` —
tenant- and user-scoped rows carrying an embedding for semantic recall.

* ``memory_item`` is the **first per-user data table**, so it lands
  both the canonical tenant-isolation RLS *and* a user-level predicate
  on ``app.user_id`` (Mini-ADR J-1 — defence in depth; the application
  store also filters by user). A single combined policy ANDs the two.

* The embedding column dimension comes from
  :data:`expert_work.persistence.embedding.EMBEDDING_DIM` — it follows
  the deployment's embedding provider and is fixed at migration time.

* An HNSW index (cosine) backs the top-k retrieval query.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import UUID

from expert_work.persistence.embedding import EMBEDDING_DIM

revision: str = "0017_long_term_memory"
down_revision: str | Sequence[str] | None = "0016_drop_app_user"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_POLICY = "memory_item_isolation"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    op.create_table(
        "memory_item",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("source_thread_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("memory_item_tenant_user_idx", "memory_item", ["tenant_id", "user_id"])
    op.execute(
        "CREATE INDEX memory_item_embedding_idx ON memory_item "
        "USING hnsw (embedding vector_cosine_ops);"
    )

    # Row-level security — tenant isolation AND a defensive user-level
    # predicate (Mini-ADR J-1). Both GUCs unset → NULLIF→NULL → deny.
    op.execute("ALTER TABLE memory_item ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE memory_item FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON memory_item;")
    op.execute(
        f"""
        CREATE POLICY {_POLICY} ON memory_item
            USING (
                tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                AND user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                AND user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
            );
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON memory_item;")
    op.drop_index("memory_item_embedding_idx", table_name="memory_item")
    op.drop_index("memory_item_tenant_user_idx", table_name="memory_item")
    op.drop_table("memory_item")
    # The ``vector`` extension is left installed — other objects may use
    # it and dropping an extension is a heavier, riskier operation.
