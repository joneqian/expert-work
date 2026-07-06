"""Stream RT-5 (PR-3b, §14) — platform quality-monitor config table.

Adds a single-row (``id == "singleton"``), platform-global, tenant-less table
storing the production quality-monitoring knobs (sampling / judge model / drift
thresholds / ``enabled`` toggle). Non-secret config; judge keys live in
``platform_provider_secret``. All fields nullable → an absent row (or null
field) falls back to the ``Settings`` env default per field; ``enabled``
defaults to off (opt-in via the UI) when no row exists.

No RLS policy: tenant-less rows, exactly like ``platform_judge_config`` — all
access goes through ``bypass_rls_session()``.

Revision id ``0119_platform_quality_config`` = 28 chars (within the 32-char
alembic ``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0119_platform_quality_config"
down_revision: str | Sequence[str] | None = "0118_quality_drift_alert"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "platform_quality_config",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("sampling_rate_pct", sa.Integer(), nullable=True),
        sa.Column("daily_cap", sa.Integer(), nullable=True),
        sa.Column("monitor_interval_s", sa.Integer(), nullable=True),
        sa.Column("monitor_batch_size", sa.Integer(), nullable=True),
        # Intervals feed ``asyncio.wait_for`` timeouts — a non-positive value
        # would tight-loop the worker. The API enforces gt=0; the CHECK restores
        # the invariant the (removed) constructor guard used to hold for any
        # out-of-band row.
        sa.CheckConstraint(
            "monitor_interval_s IS NULL OR monitor_interval_s > 0",
            name="quality_config_monitor_interval_positive",
        ),
        sa.CheckConstraint(
            "drift_interval_s IS NULL OR drift_interval_s > 0",
            name="quality_config_drift_interval_positive",
        ),
        sa.Column("judge_provider", sa.Text(), nullable=True),
        sa.Column("judge_model", sa.Text(), nullable=True),
        sa.Column("drift_interval_s", sa.Integer(), nullable=True),
        sa.Column("drift_recent_window_h", sa.Integer(), nullable=True),
        sa.Column("drift_baseline_window_h", sa.Integer(), nullable=True),
        sa.Column("drift_min_samples", sa.Integer(), nullable=True),
        sa.Column("drift_threshold", sa.Float(), nullable=True),
        sa.Column("drift_cooldown_h", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("platform_quality_config")
