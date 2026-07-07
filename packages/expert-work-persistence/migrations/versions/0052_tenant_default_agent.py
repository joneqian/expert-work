"""Stream R — tenant default agent (Mini-ADR R-9).

Revision ID: 0052_tenant_default_agent
Revises: 0051_tenant_member
Create Date: 2026-06-01

Adds 1 nullable column to ``tenant_config``:

* ``default_agent_name`` (TEXT, nullable) — the agent a tenant's members
  get by default when a thread is created without an explicit agent.
  ``NULL`` → platform fallback (``canonical-agent``), so every existing
  row keeps current behaviour.

Revision id ``0052_tenant_default_agent`` = 25 chars (within the 32-char
alembic ``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052_tenant_default_agent"
down_revision: str | Sequence[str] | None = "0051_tenant_member"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "tenant_config",
        sa.Column("default_agent_name", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_config", "default_agent_name")
