"""9.5 (distributed run queue) — agent_run ``queued`` status + ``enqueued_input``.

Adds the ``queued`` lifecycle status (a durable run owned by no process yet)
and an ``enqueued_input`` JSONB column holding the persisted run input so a
``RunQueueWorker`` on any instance can rebuild ``graph_input`` and execute it.
A partial index on ``created_at WHERE status='queued'`` backs the FIFO scan.

Revision id ``0082_agent_run_queue`` = 20 chars (within the 32-char alembic
``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0082_agent_run_queue"
down_revision: str | Sequence[str] | None = "0081_agent_run_lease"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_CHECK = "agent_run_status_valid"
_QUEUE_INDEX = "ix_agent_run_queue_scan"
_STATUS_WITH_QUEUED = (
    "('pending', 'queued', 'running', 'success', 'error', 'timeout', 'interrupted', 'paused')"
)
_STATUS_WITHOUT_QUEUED = (
    "('pending', 'running', 'success', 'error', 'timeout', 'interrupted', 'paused')"
)


def upgrade() -> None:
    op.add_column("agent_run", sa.Column("enqueued_input", JSONB(), nullable=True))
    op.drop_constraint(_CHECK, "agent_run", type_="check")
    op.create_check_constraint(_CHECK, "agent_run", f"status IN {_STATUS_WITH_QUEUED}")
    op.create_index(
        _QUEUE_INDEX,
        "agent_run",
        ["created_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )


def downgrade() -> None:
    op.drop_index(_QUEUE_INDEX, table_name="agent_run")
    op.drop_constraint(_CHECK, "agent_run", type_="check")
    op.create_check_constraint(_CHECK, "agent_run", f"status IN {_STATUS_WITHOUT_QUEUED}")
    op.drop_column("agent_run", "enqueued_input")
