"""Stream H.3 PR 2 — `agent_run.trace_id` 持久化(Mini-ADR H-9.5).

Revision ID: 0037_agent_run_trace_id
Revises: 0036_token_usage
Create Date: 2026-05-26

Until now, OTel trace_id only lived in the SSE ``metadata`` frame
(read at runtime by the client) and the agent_run row had no link to
the trace. A terminal run that completed yesterday has no trace_id at
all in any expert-work-owned record — Langfuse / Tempo can be searched by
``run_id`` but the human path is "copy run_id → paste into Tempo"
rather than "click 'Open in Langfuse' on the run detail page".

This migration adds the column + a partial index so:

* Any persisted run (running / paused / terminal / 1 year old) carries
  the trace_id that produced it.
* Reverse lookup ``Langfuse trace_id → expert_work run_id`` is fast (partial
  index skips NULL rows so it doesn't bloat the B-tree).

Schema:

* ``trace_id varchar(32) NULL`` — ``current_trace_id_hex()`` returns 16
  bytes hex = 32 chars; NULL for legacy rows + auto-triggered runs
  (J.10 trigger scheduler / J.13a curation worker passing ``None``).
* ``idx_agent_run_trace_id (trace_id) WHERE trace_id IS NOT NULL`` —
  partial index avoids the bulk of NULL rows (older data) bloating the
  B-tree.

Both steps are expand-only (forward-only). Downgrade drops the index
then the column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0037_agent_run_trace_id"
down_revision = "0036_token_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_run",
        sa.Column("trace_id", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "idx_agent_run_trace_id",
        "agent_run",
        ["trace_id"],
        unique=False,
        postgresql_where=sa.text("trace_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_agent_run_trace_id", table_name="agent_run")
    op.drop_column("agent_run", "trace_id")
