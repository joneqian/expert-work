"""thread_meta.title — human label for the session-history list.

Playground session-history uplift (PR1). Adds a nullable ``title`` column,
auto-set from the first user message and manually overridable. Pre-existing
threads keep NULL (the UI falls back to the thread_id prefix).

Search is a plain ``title ILIKE`` scan (per-tenant volume is small); a GIN
trigram index is deferred until volume warrants it.

Revision ID: 0103_thread_meta_title
Revises: 0102_platform_tool_budget
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0103_thread_meta_title"
down_revision: str | Sequence[str] | None = "0102_platform_tool_budget"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "thread_meta"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("title", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, "title")
