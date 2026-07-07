"""Capability Uplift Sprint #1 — tenant_config.trigger_fire_scan_mode.

Revision ID: 0039_trigger_fire_scan_mode
Revises: 0038_run_event
Create Date: 2026-05-27

See ``docs/streams/STREAM-UPLIFT-DESIGN.md`` § 2 (Mini-ADR U-2).

Per-tenant knob for what happens when a trigger's fire-time prompt
matches the ``context`` threat-pattern scope (e.g. the trigger row was
mutated bypassing the API after creation):

* ``warn`` — emit ``trigger:prompt_injection_warn`` and fire anyway.
  Platform-wide default; pairs with a SecOps recording-rule alert on
  the warn rate so genuine drift surfaces without blocking business.
* ``block`` — emit ``trigger:prompt_injection_blocked`` and refuse to
  fire. Opt-in for high-compliance tenants accepting the false-block
  risk that a pattern-set update can retroactively reject a previously
  ACCEPTED trigger.

This migration is metadata-only on Postgres ≥ 11 (ADD COLUMN with a
constant DEFAULT does not rewrite the table). A CHECK constraint pins
the two valid values so admin clients fail fast.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_trigger_fire_scan_mode"
down_revision: str | Sequence[str] | None = "0038_run_event"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "tenant_config",
        sa.Column(
            "trigger_fire_scan_mode",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'warn'"),
        ),
    )
    op.create_check_constraint(
        "tenant_config_trigger_fire_scan_mode_ck",
        "tenant_config",
        "trigger_fire_scan_mode IN ('warn', 'block')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "tenant_config_trigger_fire_scan_mode_ck",
        "tenant_config",
        type_="check",
    )
    op.drop_column("tenant_config", "trigger_fire_scan_mode")
