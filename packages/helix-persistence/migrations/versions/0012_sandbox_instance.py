"""Stream F.1 — sandbox_instance table.

Revision ID: 0012_sandbox_instance
Revises: 0011_tool_config
Create Date: 2026-05-16

The Sandbox Supervisor's record of every ``exec_python`` sandbox
container. Modelled on subsystems/14 § 3.2, adapted to the M0
cold-start scope (STREAM-F-DESIGN § 4.6):

* ``image_ref`` rather than ``image_layer_key`` — M0 has no image
  layer cache (M1-A), so the row carries the full image reference.
* No ``isolation_level`` column — M0 sandboxes are always ``shared``
  (STREAM-F-DESIGN § 4.1: the M0 AcquireRequest drops that branch).
* ``destroy_reason`` rather than ``evict_reason`` — covers both the
  routine ``release`` and the forced reaper / cancel destroys.

The two indexes serve the two M0 hot queries: the per-tenant active
count for the F.1 quota check, and the orphan scan the TTL reaper runs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0012_sandbox_instance"
down_revision: str | Sequence[str] | None = "0011_tool_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "sandbox_instance",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("image_ref", sa.Text(), nullable=False),
        sa.Column("node", sa.Text(), nullable=False),
        # NULL while CREATING — set once `docker run` returns an id.
        sa.Column("container_id", sa.Text(), nullable=True),
        # CREATING / IN_USE / DESTROYED / FAILED — STREAM-F-DESIGN § 2.2.
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("cpu_quota", sa.Numeric(4, 2), nullable=False),
        sa.Column("memory_mb", sa.Integer(), nullable=False),
        sa.Column("pids_limit", sa.Integer(), nullable=False),
        sa.Column("timeout_s", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("destroyed_at", sa.DateTime(timezone=True), nullable=True),
        # release / idle_timeout / cancelled / oom / unhealthy — NULL until terminal.
        sa.Column("destroy_reason", sa.Text(), nullable=True),
    )
    # Per-tenant active-count for the quota check.
    op.create_index(
        "sandbox_instance_tenant_state_idx",
        "sandbox_instance",
        ["tenant_id", "state"],
    )
    # Orphan scan for the TTL reaper (state=IN_USE, old acquired_at).
    op.create_index(
        "sandbox_instance_state_acquired_idx",
        "sandbox_instance",
        ["state", "acquired_at"],
    )


def downgrade() -> None:
    op.drop_index("sandbox_instance_state_acquired_idx", table_name="sandbox_instance")
    op.drop_index("sandbox_instance_tenant_state_idx", table_name="sandbox_instance")
    op.drop_table("sandbox_instance")
