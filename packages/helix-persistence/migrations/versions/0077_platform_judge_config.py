"""Stream PI-3-A1 — platform judge-model config table.

Adds a single-row (``id == "singleton"``), platform-global, tenant-less table
storing the platform's chosen output/action **judge** provider+model. Non-secret
config (provider/model names only); keys live in ``platform_provider_secret``.
An absent row means "not configured" → the judge falls back to each agent's own
primary model.

No RLS policy: tenant-less rows, exactly like ``platform_embedding_config`` —
all access goes through ``bypass_rls_session()``.

Revision id ``0077_platform_judge_config`` = 26 chars (within the 32-char
alembic ``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0077_platform_judge_config"
down_revision: str | Sequence[str] | None = "0076_eval_run"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "platform_judge_config",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("judge_provider", sa.Text(), nullable=True),
        sa.Column("judge_model", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("platform_judge_config")
