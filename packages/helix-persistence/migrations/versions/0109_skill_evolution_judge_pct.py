"""SE-16 PR-4 — implicit-candidate judge sample rate (SE-A45).

``tenant_config.skill_evolution_judge_sample_pct`` (default 5, 0-100):
what fraction of ``implicit_success`` candidates the evolution worker
screens through the cheap aux quality judge before distilling. Bounds
the flywheel's aux + distillation spend on the abundant implicit pool.

Revision ID: 0109_skill_evolution_judge_pct
Revises: 0108_implicit_success_signal
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0109_skill_evolution_judge_pct"
down_revision: str | Sequence[str] | None = "0108_implicit_success_signal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_CONSTRAINT = "tenant_config_judge_sample_pct_range"


def upgrade() -> None:
    op.add_column(
        "tenant_config",
        sa.Column(
            "skill_evolution_judge_sample_pct",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("5"),
        ),
    )
    op.create_check_constraint(
        _CONSTRAINT,
        "tenant_config",
        "skill_evolution_judge_sample_pct BETWEEN 0 AND 100",
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "tenant_config", type_="check")
    op.drop_column("tenant_config", "skill_evolution_judge_sample_pct")
