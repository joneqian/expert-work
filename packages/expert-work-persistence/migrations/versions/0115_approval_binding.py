"""RT-6 Tier A — agent_approval.binding_digest (RT-ADR-19).

Adds one nullable column carrying the canonical args digest bound at approval
mint. Re-verified before dispatch; a mismatch means the checkpointed tool_call
drifted from what the human approved → the resume rejects it (integrity veto).
On ``modify`` the ``mark_decided`` CAS overwrites it with the digest of the
modified args. NULL = legacy / pre-feature row (unbound), verification skipped.

Revision id ``0115_approval_binding`` = 21 chars (within the 32-char alembic
``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0115_approval_binding"
down_revision: str | Sequence[str] | None = "0114_agent_disable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column("agent_approval", sa.Column("binding_digest", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_approval", "binding_digest")
