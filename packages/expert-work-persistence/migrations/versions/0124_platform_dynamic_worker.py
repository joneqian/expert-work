"""B3 PR2 — platform dynamic-worker limits config table.

Adds a single-row (``id == "singleton"``), platform-global, tenant-less table
storing the ``dynamic_worker`` limits: ``max_concurrent``, ``max_per_run``,
and ``max_iterations``. An absent row means "not configured" → the platform
falls back to its built-in defaults.

No RLS policy: tenant-less row, exactly like ``platform_tool_budget_config``
— all access goes through ``bypass_rls_session()``.

Revision id ``0124_platform_dynamic_worker`` = 27 chars (within the 32-char
alembic ``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0124_platform_dynamic_worker"
down_revision: str | Sequence[str] | None = "0123_http_tool_denylist"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "platform_dynamic_worker_config",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("max_concurrent", sa.Integer(), nullable=False),
        sa.Column("max_per_run", sa.Integer(), nullable=False),
        sa.Column("max_iterations", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("platform_dynamic_worker_config")
