"""memory_item.agent_name — per-agent episodic memory isolation.

Stream Agent-Templates (M1-5c). Adds the owning agent for ``episodic`` memory so
one agent's events do not leak into another agent's recall; ``fact`` rows leave it
NULL (agent-agnostic user profile, shared across all of a user's agents). Recall
scopes episodic by ``(agent_name IS NULL OR agent_name = :agent)``.

The dedup unique index gains ``COALESCE(agent_name, '')`` so the same content as a
fact still dedups (NULL→''), while the same content as episodic for two different
agents is kept as two distinct memories.

Revision ID: 0098_memory_item_agent_name
Revises: 0097_agent_instance
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0098_memory_item_agent_name"
down_revision: str | Sequence[str] | None = "0097_agent_instance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "memory_item"
_DEDUP_INDEX = "memory_item_dedup_uniq"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("agent_name", sa.Text(), nullable=True))
    # Rebuild the dedup unique to include the agent (COALESCE so fact rows, with
    # NULL agent, still dedup on content).
    op.execute(f"DROP INDEX IF EXISTS {_DEDUP_INDEX};")
    op.execute(
        f"CREATE UNIQUE INDEX {_DEDUP_INDEX} ON {_TABLE} "
        "(tenant_id, user_id, content_hash, COALESCE(agent_name, '')) "
        "WHERE deleted_at IS NULL;"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_DEDUP_INDEX};")
    op.execute(
        f"CREATE UNIQUE INDEX {_DEDUP_INDEX} ON {_TABLE} "
        "(tenant_id, user_id, content_hash) WHERE deleted_at IS NULL;"
    )
    op.drop_column(_TABLE, "agent_name")
