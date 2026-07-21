"""P5b-1 fix (I-1) — memory_item dedup 唯一索引排除 bi-temporal 隐藏行.

Migration 0126 added the bi-temporal columns (``invalid_at`` / ``expired_at``)
and made ``retrieve()`` hide superseded / world-expired rows, but the dedup
partial unique index built in migration 0098 still only excludes
``deleted_at IS NOT NULL`` — a superseded-but-not-deleted row still occupies
its ``(tenant_id, user_id, content_hash, agent_name)`` slot. Consequence:
``SqlMemoryStore.supersede()``'s plain INSERT raises ``IntegrityError`` when a
reconcile re-asserts a fact whose content hash matches an already
superseded/expired (but not deleted) row — the fact is silently lost. Rebuild
the index to also exclude ``invalid_at`` / ``expired_at`` rows.

Revision ID: 0127_memory_dedup_bitemporal
Revises: 0126_memory_bitemporal
Create Date: 2026-07-21
"""

from __future__ import annotations

from alembic import op

revision = "0127_memory_dedup_bitemporal"
down_revision = "0126_memory_bitemporal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS memory_item_dedup_uniq;")
    op.execute(
        "CREATE UNIQUE INDEX memory_item_dedup_uniq ON memory_item "
        "(tenant_id, user_id, content_hash, COALESCE(agent_name, '')) "
        "WHERE deleted_at IS NULL AND invalid_at IS NULL AND expired_at IS NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS memory_item_dedup_uniq;")
    op.execute(
        "CREATE UNIQUE INDEX memory_item_dedup_uniq ON memory_item "
        "(tenant_id, user_id, content_hash, COALESCE(agent_name, '')) "
        "WHERE deleted_at IS NULL;"
    )
