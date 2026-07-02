"""SE-16 PR-2 — skill-evolution rollout gate + distillation retry budget.

- ``tenant_config.skill_evolution_enabled`` (default false): per-tenant
  rollout gate, ANDed with the platform master switch — production rolls
  out tenant by tenant (SE-A41).
- ``curation_candidate.retry_count`` (default 0): a distillation attempt
  that died on a transient fault (aux LLM timeout / rate limit /
  connection) bumps this instead of burning the candidate via
  ``evolved_at``; the worker gives up at 3 (SE-A40).

Revision ID: 0107_skill_evolution_gray_retry
Revises: 0106_thread_message_mirror
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0107_skill_evolution_gray_retry"
down_revision: str | Sequence[str] | None = "0106_thread_message_mirror"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "tenant_config",
        sa.Column(
            "skill_evolution_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "curation_candidate",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("curation_candidate", "retry_count")
    op.drop_column("tenant_config", "skill_evolution_enabled")
