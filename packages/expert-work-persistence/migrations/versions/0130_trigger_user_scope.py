"""agent_trigger user 维度 —— 唯一约束分 user + 投递路由列(Spec 1 PR1).

Revision ID: 0130_trigger_user_scope
Revises: 0129_tenant_cfg_predictive
Create Date: 2026-07-22

去 (tenant, agent_name, name) 全局唯一 → 双 partial unique index:
非空 user_id 含 user_id(两用户可同名任务),空 user_id 保留按名唯一
(manifest/legacy 无主任务)。加 originating_thread_id / context_mode
(投递路由,PR3 D1 用),context_mode 默认 fresh_thread_per_run(现行为)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0130_trigger_user_scope"
down_revision: str | Sequence[str] | None = "0129_tenant_cfg_predictive"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_CONTEXT_MODES = "('reuse_thread', 'fresh_thread_per_run')"


def upgrade() -> None:
    # 1. 投递路由列。
    op.add_column(
        "agent_trigger",
        sa.Column(
            "originating_thread_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_trigger",
        sa.Column(
            "context_mode",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'fresh_thread_per_run'"),
        ),
    )
    op.create_check_constraint(
        "agent_trigger_context_mode_valid",
        "agent_trigger",
        f"context_mode IN {_CONTEXT_MODES}",
    )

    # 2. 去全局唯一约束,换双 partial unique index。
    op.drop_constraint("agent_trigger_name_uniq", "agent_trigger", type_="unique")
    op.create_index(
        "ix_agent_trigger_user_name_uniq",
        "agent_trigger",
        ["tenant_id", "agent_name", "user_id", "name"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index(
        "ix_agent_trigger_null_user_name_uniq",
        "agent_trigger",
        ["tenant_id", "agent_name", "name"],
        unique=True,
        postgresql_where=sa.text("user_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_trigger_null_user_name_uniq", table_name="agent_trigger")
    op.drop_index("ix_agent_trigger_user_name_uniq", table_name="agent_trigger")
    op.create_unique_constraint(
        "agent_trigger_name_uniq", "agent_trigger", ["tenant_id", "agent_name", "name"]
    )
    op.drop_constraint("agent_trigger_context_mode_valid", "agent_trigger", type_="check")
    op.drop_column("agent_trigger", "context_mode")
    op.drop_column("agent_trigger", "originating_thread_id")
