"""SE-16 PR-6 — ``token_usage.usage_kind`` (SE-A43).

Distinguishes what spent the tokens: ``conversation`` (the historical
per-run LLM-call path — backfilled onto every existing row via the
server default) vs ``skill_evolution`` (the evolution flywheel's aux
calls + with/without replay). Plain Text, no CHECK — new kinds (memory
consolidation, eval) join without migration churn; the protocol-side
Literal is the value registry.

Revision ID: 0110_token_usage_kind
Revises: 0109_skill_evolution_judge_pct
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0110_token_usage_kind"
down_revision: str | Sequence[str] | None = "0109_skill_evolution_judge_pct"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "token_usage",
        sa.Column(
            "usage_kind",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'conversation'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("token_usage", "usage_kind")
