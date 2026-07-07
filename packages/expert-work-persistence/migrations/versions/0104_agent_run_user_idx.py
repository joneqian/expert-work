"""Runs filter-by-user — index ``agent_run`` by (tenant, user, created).

Revision ID: 0104_agent_run_user_idx
Revises: 0103_thread_meta_title
Create Date: 2026-07-01

``GET /v1/runs?user_id=…`` (the AdminUI Runs "filter by member" view) narrows a
tenant's runs to one end-user. Without an index that is a seq-scan over every
run in the tenant. A partial composite index ``(tenant_id, user_id,
created_at DESC) WHERE user_id IS NOT NULL`` serves the filter + the newest-first
ordering in one scan and skips system / auto-triggered runs (``user_id NULL``)
so it doesn't bloat the B-tree.

Expand-only (forward). Downgrade drops the index.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0104_agent_run_user_idx"
down_revision = "0103_thread_meta_title"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_agent_run_tenant_user_created",
        "agent_run",
        ["tenant_id", "user_id", sa.text("created_at DESC")],
        unique=False,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_run_tenant_user_created", table_name="agent_run")
