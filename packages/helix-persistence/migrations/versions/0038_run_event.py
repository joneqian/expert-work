"""Stream H.3 PR 3 — ``run_event`` table (Mini-ADR H-7, decisions A + C).

Revision ID: 0038_run_event
Revises: 0037_agent_run_trace_id
Create Date: 2026-05-26

Per-frame durable mirror of the SSE stream so RunDetail's Event stream
panel (Stream H.3 PR 4) can replay terminal runs past the
``StreamBridge`` 60 s cleanup window. Producer side: ``run_agent`` dual-
writes to ``bridge.publish`` AND ``RunEventStore.append`` on every frame.
Consumer side: live runs go through ``bridge.subscribe`` (decision A:
``run_event.seq`` + ``created_at_ms`` reproduce the same SSE wire id
``"{ms}-{seq}"`` the bridge emits), terminal runs go through
``RunEventStore.list``.

FK = ``ON DELETE RESTRICT`` (decision C) — M1's archive-then-delete
sweep must drain ``run_event`` before deleting the parent ``agent_run``
row. RESTRICT prevents an accidental cascade that would lose history
before the archive step runs.

The schema mirrors the in-memory ``StreamBridge.memory.py`` event log
(see ``_RunStream.events`` + ``_next_id``); ``(run_id, seq)`` is the
natural primary key and covers the only query pattern:
``WHERE run_id = ? AND seq >= ? ORDER BY seq ASC``.

Capacity: ~20-60 events x ~500 bytes ≈ 10-30 KB/run. 1000 runs/day →
11 GB/year — acceptable until M1's retention sweep (~30 days, aligned
with ``event_log``).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0038_run_event"
down_revision: str | Sequence[str] | None = "0037_agent_run_trace_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "run_event",
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_run.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("event_name", sa.Text(), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        # Millisecond epoch — feeds SSE id 重组 (decision A;
        # ``StreamBridge.memory._next_id`` 用同型 ``f"{ms}-{seq}"``).
        sa.Column("created_at_ms", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("run_id", "seq", name="pk_run_event"),
    )
    # Primary key already covers the ``WHERE run_id = ? ORDER BY seq``
    # pattern; no additional indexes (decision: keep schema minimal,
    # M1 retention sweep joins via agent_run.finished_at which has its
    # own index).

    # Tenant RLS — runs are tenant-scoped through the FK ``run_id``;
    # the policy walks the parent row.
    op.execute("ALTER TABLE run_event ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY run_event_tenant_isolation ON run_event "
        "USING (run_id IN ("
        "  SELECT id FROM agent_run "
        "  WHERE tenant_id = current_setting('app.tenant_id', true)::uuid"
        "))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS run_event_tenant_isolation ON run_event")
    op.drop_table("run_event")
