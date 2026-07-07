"""token_usage.user_id — per-user cost attribution.

Stream Agent-Templates (M1-5a). Adds the end-user (``tenant_user.id``) an LLM call
ran for, so cost can be attributed per user (the external-app per-user run model),
not only per ``(tenant, agent, model)``. NULL for runs with no user context /
legacy rows. Additive + nullable → no backfill. A ``(tenant_id, user_id,
observed_at)`` index backs the per-user rollup.

Revision ID: 0096_token_usage_user_id
Revises: 0095_platform_agent_template
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0096_token_usage_user_id"
down_revision: str | Sequence[str] | None = "0095_platform_agent_template"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "token_usage"
_INDEX = "token_usage_tenant_user_time_idx"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("user_id", UUID(as_uuid=True), nullable=True))
    op.execute(f"CREATE INDEX {_INDEX} ON {_TABLE} (tenant_id, user_id, observed_at DESC)")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    op.drop_column(_TABLE, "user_id")
