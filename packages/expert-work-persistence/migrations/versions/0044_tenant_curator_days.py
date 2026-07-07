"""Capability Uplift Sprint #4 — per-tenant Curator threshold columns.

Revision ID: 0044_tenant_curator_days
Revises: 0043_skill_curator_columns
Create Date: 2026-05-28

Adds 2 columns to ``tenant_config`` for the per-tenant Curator
thresholds (Mini-ADR U-28):

* ``skill_stale_days`` (INTEGER, default 30, CHECK 1..365) — number of
  days an ``active`` skill must go without bind / view activity before
  the Curator transitions it to ``stale``.

* ``skill_archive_days`` (INTEGER, default 90, CHECK 2..730, CHECK
  ``> skill_stale_days``) — number of days a ``stale`` skill must
  remain inactive before the Curator transitions it to ``archived``.

Defaults derive from external skill-marketplace observations; M1-K
J.7b-1 will revisit after 2-4 weeks of real agent-self-create data
(per ``capability-uplift-plan.md`` Week 11-12).

The cross-field invariant (``skill_archive_days > skill_stale_days``)
is enforced both here (DB CHECK) and at the Pydantic level
(``TenantConfigRecord.@model_validator``) so an admin can't accidentally
create a config where a skill goes stale and archives on the same day.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_tenant_curator_days"
down_revision: str | Sequence[str] | None = "0043_skill_curator_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "tenant_config",
        sa.Column(
            "skill_stale_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("30"),
        ),
    )
    op.add_column(
        "tenant_config",
        sa.Column(
            "skill_archive_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("90"),
        ),
    )
    op.create_check_constraint(
        "tenant_config_skill_stale_days_range_ck",
        "tenant_config",
        "skill_stale_days BETWEEN 1 AND 365",
    )
    op.create_check_constraint(
        "tenant_config_skill_archive_days_range_ck",
        "tenant_config",
        "skill_archive_days BETWEEN 2 AND 730",
    )
    op.create_check_constraint(
        "tenant_config_skill_archive_gt_stale_ck",
        "tenant_config",
        "skill_archive_days > skill_stale_days",
    )


def downgrade() -> None:
    op.drop_constraint("tenant_config_skill_archive_gt_stale_ck", "tenant_config", type_="check")
    op.drop_constraint("tenant_config_skill_archive_days_range_ck", "tenant_config", type_="check")
    op.drop_constraint("tenant_config_skill_stale_days_range_ck", "tenant_config", type_="check")
    op.drop_column("tenant_config", "skill_archive_days")
    op.drop_column("tenant_config", "skill_stale_days")
