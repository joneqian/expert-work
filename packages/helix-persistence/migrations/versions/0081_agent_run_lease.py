"""9.4 (HA failover) — agent_run ownership lease (claimed_by / lease_until / heartbeat_at).

Adds the run-ownership lease columns so a crashed control-plane instance's
in-flight runs become detectable orphans: the executing instance renews
``lease_until`` via ``heartbeat_at`` touches; once the lease expires a peer
instance's orphan sweep reclaims the run and resumes it from its durable
checkpoint. A partial index on ``lease_until WHERE status='running'`` backs the
sweep scan.

Revision id ``0081_agent_run_lease`` = 20 chars (within the 32-char alembic
``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0081_agent_run_lease"
down_revision: str | Sequence[str] | None = "0080_approval_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_SWEEP_INDEX = "ix_agent_run_lease_sweep"


def upgrade() -> None:
    op.add_column("agent_run", sa.Column("claimed_by", sa.Text(), nullable=True))
    op.add_column("agent_run", sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_run", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "agent_run",
        sa.Column("reclaim_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index(
        _SWEEP_INDEX,
        "agent_run",
        ["lease_until"],
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index(_SWEEP_INDEX, table_name="agent_run")
    op.drop_column("agent_run", "reclaim_count")
    op.drop_column("agent_run", "heartbeat_at")
    op.drop_column("agent_run", "lease_until")
    op.drop_column("agent_run", "claimed_by")
