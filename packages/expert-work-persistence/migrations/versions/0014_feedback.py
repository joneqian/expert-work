"""Stream G.6 — user feedback table.

Revision ID: 0014_feedback
Revises: 0013_credential_proxy
Create Date: 2026-05-18

A tenant-scoped ``feedback`` table — one row per 👍/👎 a user leaves on
an agent session (or a specific turn). RLS-enabled with the canonical
tenant-isolation policy, identical to migration 0005.

``turn_seq`` points at ``event_log.seq`` but carries no foreign key:
event_log rows are cold-archived (Stream G.8) and an FK would block the
archival delete. ``trace_id`` correlates the feedback to its W3C trace.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0014_feedback"
down_revision: str | Sequence[str] | None = "0013_credential_proxy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_POLICY = "feedback_tenant_isolation"


def upgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", UUID(as_uuid=True), nullable=False),
        # event_log.seq of the rated turn; NULL = feedback on the whole
        # session. No FK — event_log is cold-archived (G.8).
        sa.Column("turn_seq", sa.BigInteger(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        # 'up' | 'down'.
        sa.Column("rating", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("feedback_tenant_thread_idx", "feedback", ["tenant_id", "thread_id"])
    op.create_index(
        "feedback_tenant_time_idx",
        "feedback",
        ["tenant_id", sa.text("created_at DESC")],
    )

    # Row-level security — the canonical tenant-isolation policy (0005).
    op.execute("ALTER TABLE feedback ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE feedback FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON feedback;")
    op.execute(
        f"""
        CREATE POLICY {_POLICY} ON feedback
            USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON feedback;")
    op.drop_index("feedback_tenant_time_idx", table_name="feedback")
    op.drop_index("feedback_tenant_thread_idx", table_name="feedback")
    op.drop_table("feedback")
