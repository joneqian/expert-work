"""13.2 — agent_approval idempotent resume (idempotency_key + continuation_run_id).

Adds two nullable columns written atomically with the ``mark_decided`` CAS so a
retried / concurrent resume carrying the same ``idempotency_key`` reads back the
``continuation_run_id`` and replays it instead of 409'ing — deterministic
recovery for the J.8 approval-resume path.

Revision id ``0080_approval_idempotency`` = 24 chars (within the 32-char
alembic ``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0080_approval_idempotency"
down_revision: str | Sequence[str] | None = "0079_role_binding_conditions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column("agent_approval", sa.Column("idempotency_key", sa.Text(), nullable=True))
    op.add_column(
        "agent_approval",
        sa.Column("continuation_run_id", PG_UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_approval", "continuation_run_id")
    op.drop_column("agent_approval", "idempotency_key")
