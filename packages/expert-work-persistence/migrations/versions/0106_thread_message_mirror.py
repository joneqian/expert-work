"""thread_message mirror — conversation full-text search (conversation IA M4).

Message history lives in LangGraph's ``checkpoints`` blob (no SQL pushdown,
no tenant_id/RLS), so content search needs a write-side mirror:

- ``thread_message`` — one row per user/assistant turn, ``(thread_id, seq)``
  PK (``seq`` = index in the checkpoint's append-only ``messages`` channel,
  stable). Trigram GIN index accelerates the browser's ``ILIKE '%q%'``
  content search (same substring semantics as the title search; tsvector
  FTS rejected — no CJK tokenisation).
- ``thread_message_sync`` — per-thread mirror watermark for the
  TranscriptMirrorSweep; a missing row *is* the backfill queue, so history
  converges without a one-off script.

Both tables carry the canonical tenant-isolation RLS (ENABLE+FORCE); the
sweep writes cross-tenant via ``bypass_rls_session`` (Stream N contract).

Revision ID: 0106_thread_message_mirror
Revises: 0105_thread_agent_user_idx
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0106_thread_message_mirror"
down_revision: str | Sequence[str] | None = "0105_thread_agent_user_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_MSG = "thread_message"
_MSG_POLICY = "thread_message_tenant_isolation"
_SYNC = "thread_message_sync"
_SYNC_POLICY = "thread_message_sync_tenant_isolation"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    op.create_table(
        _MSG,
        sa.Column(
            "thread_id",
            UUID(as_uuid=True),
            sa.ForeignKey("thread_meta.thread_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("seq", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("thread_message_tenant_idx", _MSG, ["tenant_id"])
    op.execute(
        f"CREATE INDEX thread_message_content_trgm_idx ON {_MSG} USING gin (content gin_trgm_ops);"
    )

    op.create_table(
        _SYNC,
        sa.Column(
            "thread_id",
            UUID(as_uuid=True),
            sa.ForeignKey("thread_meta.thread_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
    )

    for table, policy in ((_MSG, _MSG_POLICY), (_SYNC, _SYNC_POLICY)):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table};")
        op.execute(
            f"""
            CREATE POLICY {policy} ON {table}
                USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
            """
        )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_SYNC_POLICY} ON {_SYNC};")
    op.drop_table(_SYNC)
    op.execute(f"DROP POLICY IF EXISTS {_MSG_POLICY} ON {_MSG};")
    op.execute("DROP INDEX IF EXISTS thread_message_content_trgm_idx;")
    op.drop_index("thread_message_tenant_idx", table_name=_MSG)
    op.drop_table(_MSG)
