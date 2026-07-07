"""Stream J.15 — sandbox_instance.last_used_at for warm per-user sessions.

Revision ID: 0020_sandbox_last_used
Revises: 0019_artifact
Create Date: 2026-05-19

A per-user sandbox is now a *warm session* — it stays ``IN_USE`` across
runs / messages and is reaped only after an idle period (STREAM-J-DESIGN
§ 9, Mini-ADR J-10). ``last_used_at`` records the time of the last
``exec``; the supervisor's reaper reclaims a session once
``last_used_at`` is older than ``session_idle_ttl_s``.

Nullable — a sandbox acquired but never exec'd has no ``last_used_at``;
the reaper falls back to ``acquired_at`` for those.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_sandbox_last_used"
down_revision: str | Sequence[str] | None = "0019_artifact"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "sandbox_instance",
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sandbox_instance", "last_used_at")
