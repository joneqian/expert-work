"""Stream 12.4 — platform billing-rollup config table.

Adds a single-row (``id == "singleton"``), platform-global, tenant-less table
holding the platform billing toggles read by the offline billing-rollup job.
For now one flag: ``rollup_enabled`` — when ``false`` the cron-driven
``BillingRollupJob`` skips its run, so a platform operator can pause cost rollup
from the admin UI without touching the k8s CronJob. Default ``true`` keeps the
job running for existing deployments.

No RLS policy: tenant-less rows, exactly like ``platform_judge_config`` /
``platform_embedding_config`` — all access goes through ``bypass_rls_session()``.

Revision id ``0083_platform_billing_config`` = 28 chars (within the 32-char
alembic ``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0083_platform_billing_config"
down_revision: str | Sequence[str] | None = "0082_agent_run_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "platform_billing_config",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "rollup_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("platform_billing_config")
