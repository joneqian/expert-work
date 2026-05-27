"""Capability Uplift Sprint #6 — tenant_config.memory_recall_mode.

Revision ID: 0041_memory_recall_mode
Revises: 0040_memory_item_content_tsv
Create Date: 2026-05-27

See ``docs/streams/STREAM-UPLIFT-DESIGN.md`` § 7 (Mini-ADR U-5).

Per-tenant escape hatch for hybrid vs vector-only memory recall:

* ``hybrid`` (default) — :meth:`MemoryStore.retrieve` runs vector +
  Postgres full-text and fuses via Reciprocal Rank Fusion (k=60).
* ``vector`` — legacy pure-pgvector cosine path. Opt-in for tenants
  whose workload regressed against the K.K12 eval baseline once
  hybrid landed (low-probability but the runbook § 9 documents the
  switch path).

Mirrors the 0039 pattern (CHECK constraint pins the enum values at
the DB level so admin clients fail fast rather than tripping a
runtime mismatch). Metadata-only on Postgres ≥ 11.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_memory_recall_mode"
down_revision: str | Sequence[str] | None = "0040_memory_item_content_tsv"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "tenant_config",
        sa.Column(
            "memory_recall_mode",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'hybrid'"),
        ),
    )
    op.create_check_constraint(
        "tenant_config_memory_recall_mode_ck",
        "tenant_config",
        "memory_recall_mode IN ('hybrid', 'vector')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "tenant_config_memory_recall_mode_ck",
        "tenant_config",
        type_="check",
    )
    op.drop_column("tenant_config", "memory_recall_mode")
