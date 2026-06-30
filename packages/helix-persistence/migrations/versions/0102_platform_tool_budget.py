"""Phase 3 — platform tool-output-budget config table.

Adds a single-row (``id == "singleton"``), platform-global, tenant-less table
storing the on/off for the tool-output-budget feature (generalized
externalization + persist floor + CM-12 prune). An absent row means "not
configured" → the service falls back to the ``HELIX_TOOL_OUTPUT_BUDGET`` env
default.

No RLS policy: tenant-less rows, exactly like ``platform_judge_config`` — all
access goes through ``bypass_rls_session()``.

Revision id ``0102_platform_tool_budget`` = 25 chars (within the 32-char
alembic ``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0102_platform_tool_budget"
down_revision: str | Sequence[str] | None = "0101_knowledge_ingest_durability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "platform_tool_budget_config",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("platform_tool_budget_config")
