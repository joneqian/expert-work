"""Capability Uplift Sprint #4 — skill Curator state-machine columns.

Revision ID: 0043_skill_curator_columns
Revises: 0042_skill_supporting_files
Create Date: 2026-05-28

Adds 3 columns to ``skill`` + a new ``stale`` state to the status
CHECK + a partial index Curator's sweep query uses
(see ``docs/streams/STREAM-UPLIFT-DESIGN.md`` § 5, Mini-ADR U-25):

* ``pinned`` (BOOL, default ``false``) — operator escape hatch. Pinned
  skills are skipped by every Curator transition (forever ``active``
  unless an admin manually changes status).

* ``last_used_at`` (TIMESTAMPTZ, nullable) — throttled (1h / skill /
  process) activity timestamp bumped by ``_load_skills`` (bind) +
  ``skill_view`` (view). Backfilled to ``updated_at`` so existing M0
  rows look "recently used" and do not flip to stale on first sweep.

* ``state_changed_at`` (TIMESTAMPTZ, default ``now()``) — advances on
  every Curator transition + every manual status PATCH. Powers the
  runbook's "when did this skill go stale?" query without joining the
  audit log.

The ``status`` CHECK is widened from ``draft|active|archived`` to
``draft|active|stale|archived``. ``stale`` is the Curator's first-pass
transition; bare-name lookups auto-revive to ``active`` via the
``bump_last_used_at`` SQL.

A partial index ``ix_skill_curator_scan`` covers the Curator sweep
query (per tenant, only rows that *could* transition):
``WHERE status IN ('active', 'stale') AND pinned = false``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_skill_curator_columns"
down_revision: str | Sequence[str] | None = "0042_skill_supporting_files"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "skill",
        sa.Column(
            "pinned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "skill",
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "skill",
        sa.Column(
            "state_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Widen status CHECK to allow 'stale'. Drop + recreate is the
    # PostgreSQL-portable form (no in-place CHECK modify).
    op.drop_constraint("skill_status_check", "skill", type_="check")
    op.create_check_constraint(
        "skill_status_check",
        "skill",
        "status IN ('draft', 'active', 'stale', 'archived')",
    )

    # Backfill existing rows: last_used_at = updated_at so M0 rows
    # carry a sensible recency signal and don't immediately stale-flag
    # on the first Curator sweep. state_changed_at = updated_at for
    # the same reason — the runbook will see "the row was last touched
    # at the migration date" rather than NULL.
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE skill SET last_used_at = updated_at, state_changed_at = updated_at")
    )

    # Curator sweep path: per tenant, scan only rows that could
    # transition. Archived + draft + pinned rows are inert.
    op.create_index(
        "ix_skill_curator_scan",
        "skill",
        ["tenant_id", "status", "last_used_at"],
        postgresql_where=sa.text("status IN ('active', 'stale') AND pinned = false"),
    )


def downgrade() -> None:
    op.drop_index("ix_skill_curator_scan", table_name="skill")
    # Bring any 'stale' rows back to 'active' before narrowing the
    # CHECK — keeps the downgrade non-destructive (data preserved as
    # the closest legal pre-Sprint-#4 state).
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE skill SET status = 'active' WHERE status = 'stale'"))
    op.drop_constraint("skill_status_check", "skill", type_="check")
    op.create_check_constraint(
        "skill_status_check",
        "skill",
        "status IN ('draft', 'active', 'archived')",
    )
    op.drop_column("skill", "state_changed_at")
    op.drop_column("skill", "last_used_at")
    op.drop_column("skill", "pinned")
