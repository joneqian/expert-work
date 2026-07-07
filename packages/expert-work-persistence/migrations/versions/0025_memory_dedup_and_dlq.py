"""Stream K.K7 — memory_item.content_hash + UNIQUE + memory_writeback_dlq.

Revision ID: 0025_memory_dedup_and_dlq
Revises: 0024_memory_soft_delete
Create Date: 2026-05-20

Stream K.K7 (Mini-ADR K-5): the J.3 writeback path runs after every
run and re-extracts memories from the trajectory — re-running the same
turn would append duplicate rows. Adds:

* ``memory_item.content_hash CHAR(64)`` — SHA-256 hex of the
  *normalised* content (``strip().lower()``). Application stores
  compute the hash at write time; the migration backfills existing
  rows via PG-side ``encode(digest(...), 'hex')`` so the column can
  be ``NOT NULL``.
* ``UNIQUE (tenant_id, user_id, content_hash) WHERE deleted_at IS
  NULL`` — partial unique index keeps the constraint compatible with
  the K6 soft-delete column (a forgotten memory does not block a new
  write of the same content).
* ``memory_writeback_dlq`` — minimal dead-letter queue for writebacks
  that the LLM extraction / embed / DB-write path can't complete. The
  retention-cleanup-job's K7 retry sweep drains it.

The ``pgcrypto`` extension is enabled once here so the backfill
SHA-256 has a PG-side implementation.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0025_memory_dedup_and_dlq"
down_revision: str | Sequence[str] | None = "0024_memory_soft_delete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


_DEDUP_INDEX = "memory_item_dedup_uniq"


def upgrade() -> None:
    # pgcrypto for ``digest('text', 'sha256')`` — used here for the
    # one-shot backfill and available for any future DB-side hashing.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    op.add_column(
        "memory_item",
        sa.Column("content_hash", sa.CHAR(length=64), nullable=True),
    )
    # Backfill existing rows. Normalisation matches the Python hash
    # function ``_normalise_for_hash`` in the orchestrator — lower-case +
    # strip whitespace — so a value stored before this migration ranks
    # equal to an identical value written afterwards.
    op.execute(
        "UPDATE memory_item "
        "SET content_hash = encode(digest(lower(trim(content)), 'sha256'), 'hex') "
        "WHERE content_hash IS NULL;"
    )
    op.alter_column("memory_item", "content_hash", nullable=False)

    # Partial unique index — soft-deleted rows are excluded so a forgotten
    # memory does not block a new write of the same content.
    op.execute(
        f"CREATE UNIQUE INDEX {_DEDUP_INDEX} ON memory_item "
        "(tenant_id, user_id, content_hash) WHERE deleted_at IS NULL;"
    )

    # Dead-letter queue for writebacks that couldn't complete.
    op.create_table(
        "memory_writeback_dlq",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", PG_UUID(as_uuid=True), nullable=False),
        sa.Column("source_thread_id", sa.Text(), nullable=True),
        # JSONB carrying the extracted ``[(kind, content), ...]`` so the
        # retry worker can re-do the embed + write without needing the
        # original trajectory. Schema validated by the worker.
        sa.Column("extracted", JSONB, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "memory_writeback_dlq_ready_idx",
        "memory_writeback_dlq",
        ["next_retry_at"],
    )


def downgrade() -> None:
    op.drop_index("memory_writeback_dlq_ready_idx", table_name="memory_writeback_dlq")
    op.drop_table("memory_writeback_dlq")
    op.execute(f"DROP INDEX IF EXISTS {_DEDUP_INDEX};")
    op.drop_column("memory_item", "content_hash")
    # Leave pgcrypto installed — other migrations / objects may use it
    # and dropping the extension is destructive.
