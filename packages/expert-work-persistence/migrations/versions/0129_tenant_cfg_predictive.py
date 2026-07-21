"""P5b-2b ⑦ — tenant_config.memory_predictive_review_enabled (activation gap fix).

Revision ID: 0129_tenant_cfg_predictive
Revises: 0128_dlq_source_run_id
Create Date: 2026-07-21

Adds the ``memory_predictive_review_enabled`` column to ``tenant_config``
so tenants can actually opt in to MemoryConsolidator's SUB-PASS 3
(predictive review of facts whose ``expected_valid_days`` window came
due). The Pydantic field on
:class:`expert_work.protocol.tenant_config.TenantConfigRecord` and its
consumption in ``_resolve_thresholds`` landed at 7a8e9155 (P5b-2b T4)
without this column — every tenant permanently read the Pydantic
default (``False``), making SUB-PASS 3 dead code. Mirrors migration
0046's ``memory_purge_enabled`` add.

Note: ``0129_tenant_cfg_predictive`` revision id is 26 chars (within
the 32-char alembic ``version_num`` ceiling per
[memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0129_tenant_cfg_predictive"
down_revision: str | Sequence[str] | None = "0128_dlq_source_run_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "tenant_config",
        sa.Column(
            "memory_predictive_review_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_config", "memory_predictive_review_enabled")
