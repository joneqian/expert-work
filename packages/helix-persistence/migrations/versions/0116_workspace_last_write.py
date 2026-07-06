"""RT-6 Tier B — user_workspace.last_write_at (RT-ADR-20).

Adds one nullable timestamp bumped to now() under the workspace write lock on
every workspace mutation (write_file / edit_file / bash). A paused approval
whose ``requested_at`` predates it saw its workspace change before execution
(approve-then-swap-script drift) — surfaced audit-only, never blocks. NULL
until the first write after this column exists.

``user_workspace`` carries no RLS (migration 0018), so the raw advisory-lock
session bumps it without a tenant role.

Revision id ``0116_workspace_last_write`` = 25 chars (within the 32-char
alembic ``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0116_workspace_last_write"
down_revision: str | Sequence[str] | None = "0115_approval_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "user_workspace",
        sa.Column("last_write_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_workspace", "last_write_at")
