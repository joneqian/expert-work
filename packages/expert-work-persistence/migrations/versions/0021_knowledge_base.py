"""Stream J.5 — knowledge / RAG: knowledge_base / document / chunk tables.

Revision ID: 0021_knowledge_base
Revises: 0020_sandbox_last_used
Create Date: 2026-05-19

A tenant's ``knowledge_base`` owns ``knowledge_document`` rows (uploaded
source files), each of which owns ``knowledge_chunk`` rows (embedded
slices) — STREAM-J-DESIGN § 12.

* All three tables are **tenant-scoped only** — knowledge bases are
  tenant-shared, not per-user (unlike ``memory_item`` in ``0017``). Each
  lands the canonical tenant-isolation RLS on ``app.tenant_id``.

* No foreign keys between the tables: a FK into a ``FORCE`` ROW LEVEL
  SECURITY table is a known footgun (Mini-ADR J-1a, as ``artifact`` in
  ``0019``). ``kb_id`` / ``document_id`` are bare UUID columns; the
  ``KnowledgeStore`` cascades deletes in the application layer.

* ``knowledge_chunk.embedding`` dimension comes from
  :data:`expert_work.persistence.embedding.EMBEDDING_DIM` — fixed at
  migration time, following the deployment's embedding provider. An
  HNSW (cosine) index backs the top-k retrieval query.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import UUID

from expert_work.persistence.embedding import EMBEDDING_DIM

revision: str = "0021_knowledge_base"
down_revision: str | Sequence[str] | None = "0020_sandbox_last_used"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_BASE_POLICY = "knowledge_base_isolation"
_DOC_POLICY = "knowledge_document_isolation"
_CHUNK_POLICY = "knowledge_chunk_isolation"

#: Tenant-only predicate — GUC unset → NULLIF→NULL → deny.
_ISOLATION = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def _enable_tenant_rls(table: str, policy: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {policy} ON {table};")
    op.execute(f"CREATE POLICY {policy} ON {table} USING ({_ISOLATION}) WITH CHECK ({_ISOLATION});")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    op.create_table(
        "knowledge_base",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("tenant_id", "name", name="knowledge_base_identity_uniq"),
    )

    op.create_table(
        "knowledge_document",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kb_id", UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id", "kb_id", "filename", name="knowledge_document_identity_uniq"
        ),
    )

    op.create_table(
        "knowledge_chunk",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kb_id", UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("knowledge_chunk_kb_idx", "knowledge_chunk", ["tenant_id", "kb_id"])
    op.create_index("knowledge_chunk_document_idx", "knowledge_chunk", ["document_id"])
    op.execute(
        "CREATE INDEX knowledge_chunk_embedding_idx ON knowledge_chunk "
        "USING hnsw (embedding vector_cosine_ops);"
    )

    _enable_tenant_rls("knowledge_base", _BASE_POLICY)
    _enable_tenant_rls("knowledge_document", _DOC_POLICY)
    _enable_tenant_rls("knowledge_chunk", _CHUNK_POLICY)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_CHUNK_POLICY} ON knowledge_chunk;")
    op.execute(f"DROP POLICY IF EXISTS {_DOC_POLICY} ON knowledge_document;")
    op.execute(f"DROP POLICY IF EXISTS {_BASE_POLICY} ON knowledge_base;")
    op.drop_index("knowledge_chunk_embedding_idx", table_name="knowledge_chunk")
    op.drop_index("knowledge_chunk_document_idx", table_name="knowledge_chunk")
    op.drop_index("knowledge_chunk_kb_idx", table_name="knowledge_chunk")
    op.drop_table("knowledge_chunk")
    op.drop_table("knowledge_document")
    op.drop_table("knowledge_base")
    # The ``vector`` extension is left installed — other tables use it.
