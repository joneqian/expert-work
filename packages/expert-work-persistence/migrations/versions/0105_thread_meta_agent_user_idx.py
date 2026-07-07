"""Conversation drill-down — index ``thread_meta`` by (tenant, agent, user).

Revision ID: 0105_thread_agent_user_idx
Revises: 0104_agent_run_user_idx
Create Date: 2026-07-02

The conversation-centric IA reads threads filtered by ``(tenant_id,
agent_name[, agent_version][, user_id])`` — the agent Conversations tab,
the global conversation browser's agent+user filters, and the M2 users
rollup (``GET /v1/agents/{name}/{version}/users``). The existing
``thread_meta_tenant_user_idx (tenant_id, user_id)`` can't serve the
agent-first shape, so those reads seq-scan the tenant's threads. A
composite ``(tenant_id, agent_name, user_id)`` index serves both the
agent-only and agent+user filters (leftmost-prefix).

Expand-only (forward). Downgrade drops the index.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0105_thread_agent_user_idx"
down_revision = "0104_agent_run_user_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "thread_meta_tenant_agent_user_idx",
        "thread_meta",
        ["tenant_id", "agent_name", "user_id"],
        unique=False,
        postgresql_where=sa.text("agent_name IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("thread_meta_tenant_agent_user_idx", table_name="thread_meta")
