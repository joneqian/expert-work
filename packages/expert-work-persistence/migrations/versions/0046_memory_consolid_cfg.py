"""Capability Uplift Sprint #7 — per-tenant MemoryConsolidator thresholds.

Revision ID: 0046_memory_consolid_cfg
Revises: 0045_memory_consolidator
Create Date: 2026-05-28

Adds 4 columns to ``tenant_config`` for per-tenant MemoryConsolidator
thresholds (Mini-ADR U-38). All bounds mirror the Pydantic Field
``ge`` / ``le`` constraints on
:class:`expert_work.protocol.tenant_config.TenantConfigRecord` so an
admin client fails fast at the Pydantic layer rather than tripping a
``23514`` at INSERT time.

* ``memory_consolidation_min_cluster_size`` (INTEGER, default 3, CHECK
  2..20) — number of similar transient items required before LLM
  verification fires. N=2 too aggressive, N>5 too conservative.

* ``memory_consolidation_similarity`` (DOUBLE PRECISION, default 0.85,
  CHECK 0.7..0.99) — cosine-similarity floor (1 - cosine_distance) for
  the embedding pre-filter. Below 0.7 too permissive; above 0.99 only
  near-exact paraphrase clusters.

* ``memory_purge_enabled`` (BOOLEAN, default TRUE) — escape hatch for
  high-compliance tenants who want the consolidate path but not the
  lone-item soft-delete path.

* ``memory_purge_min_age_days`` (INTEGER, default 30, CHECK 7..365) —
  grace window during which a transient item is protected from purge
  even if never retrieved (Mini-ADR U-37 protection #1).

Note: ``0046_memory_consolid_cfg`` revision id is 24 chars (within
the 32-char alembic ``version_num`` ceiling per
[memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046_memory_consolid_cfg"
down_revision: str | Sequence[str] | None = "0045_memory_consolidator"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "tenant_config",
        sa.Column(
            "memory_consolidation_min_cluster_size",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("3"),
        ),
    )
    op.add_column(
        "tenant_config",
        sa.Column(
            "memory_consolidation_similarity",
            sa.Float(precision=2),
            nullable=False,
            server_default=sa.text("0.85"),
        ),
    )
    op.add_column(
        "tenant_config",
        sa.Column(
            "memory_purge_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "tenant_config",
        sa.Column(
            "memory_purge_min_age_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("30"),
        ),
    )
    op.create_check_constraint(
        "tenant_config_mem_cluster_size_ck",
        "tenant_config",
        "memory_consolidation_min_cluster_size BETWEEN 2 AND 20",
    )
    op.create_check_constraint(
        "tenant_config_mem_similarity_ck",
        "tenant_config",
        "memory_consolidation_similarity >= 0.7 AND memory_consolidation_similarity <= 0.99",
    )
    op.create_check_constraint(
        "tenant_config_mem_purge_days_ck",
        "tenant_config",
        "memory_purge_min_age_days BETWEEN 7 AND 365",
    )


def downgrade() -> None:
    op.drop_constraint("tenant_config_mem_purge_days_ck", "tenant_config", type_="check")
    op.drop_constraint("tenant_config_mem_similarity_ck", "tenant_config", type_="check")
    op.drop_constraint("tenant_config_mem_cluster_size_ck", "tenant_config", type_="check")
    op.drop_column("tenant_config", "memory_purge_min_age_days")
    op.drop_column("tenant_config", "memory_purge_enabled")
    op.drop_column("tenant_config", "memory_consolidation_similarity")
    op.drop_column("tenant_config", "memory_consolidation_min_cluster_size")
