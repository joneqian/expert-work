"""Stream J.12 — ``eval_dataset`` + ``curation_candidate`` tables (Mini-ADR J-43).

Revision ID: 0034_eval_dataset
Revises: 0033_agent_trigger
Create Date: 2026-05-22

J.12 学习 / 反馈闭环 (STREAM-J-DESIGN § 17). Two tables:

* ``eval_dataset`` — a curated eval case, shared with J.13. A "dataset"
  is the set of rows sharing ``(tenant_id, agent_name, name)``.
* ``curation_candidate`` — a trajectory the curation worker flagged for
  human review. Unique per ``(tenant_id, trajectory_key)`` so a
  trajectory becomes a candidate at most once; a partial index over
  ``status = 'pending'`` keeps the review-list scan cheap.

Both tables use the standard ``current_setting('app.tenant_id')`` GUC
tenant RLS — the curated dataset is scoped to ``(tenant, agent)``
(Mini-ADR J-43).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0034_eval_dataset"
down_revision: str | Sequence[str] | None = "0033_agent_trigger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_SOURCE_VALUES = "('golden', 'trajectory', 'regression')"
_OUTCOME_VALUES = "('success', 'failed', 'max_steps', 'cancelled')"
_SIGNAL_VALUES = "('negative_feedback', 'failed_outcome', 'positive_feedback')"
_RATING_VALUES = "('up', 'down')"
_STATUS_VALUES = "('pending', 'promoted', 'dismissed')"


def upgrade() -> None:
    op.create_table(
        "eval_dataset",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("input", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("expected", JSONB(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_trajectory_key", sa.Text(), nullable=True),
        sa.Column("source_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"source IN {_SOURCE_VALUES}", name="eval_dataset_source_valid"),
    )
    op.create_index("ix_eval_dataset_tenant_id", "eval_dataset", ["tenant_id"])
    op.create_index("ix_eval_dataset_agent", "eval_dataset", ["tenant_id", "agent_name"])

    op.create_table(
        "curation_candidate",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("agent_version", sa.Text(), nullable=True),
        sa.Column("thread_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("trajectory_key", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("signal", sa.Text(), nullable=False),
        sa.Column("feedback_rating", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("eval_dataset_id", UUID(as_uuid=True), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"outcome IN {_OUTCOME_VALUES}", name="curation_candidate_outcome_valid"
        ),
        sa.CheckConstraint(f"signal IN {_SIGNAL_VALUES}", name="curation_candidate_signal_valid"),
        sa.CheckConstraint(
            f"feedback_rating IN {_RATING_VALUES}", name="curation_candidate_rating_valid"
        ),
        sa.CheckConstraint(f"status IN {_STATUS_VALUES}", name="curation_candidate_status_valid"),
        # A trajectory becomes a candidate at most once — the worker's
        # insert-if-absent upsert key.
        sa.UniqueConstraint(
            "tenant_id", "trajectory_key", name="curation_candidate_trajectory_uniq"
        ),
    )
    op.create_index("ix_curation_candidate_tenant_id", "curation_candidate", ["tenant_id"])
    op.create_index(
        "ix_curation_candidate_agent", "curation_candidate", ["tenant_id", "agent_name"]
    )
    # Partial index — the review-list query scans only pending candidates.
    op.create_index(
        "ix_curation_candidate_pending",
        "curation_candidate",
        ["tenant_id", "agent_name"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    # Tenant RLS — same ``app.tenant_id`` GUC pattern as agent_trigger,
    # agent_run, agent_approval, artifact.
    for table in ("eval_dataset", "curation_candidate"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in ("curation_candidate", "eval_dataset"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.drop_index("ix_curation_candidate_pending", table_name="curation_candidate")
    op.drop_index("ix_curation_candidate_agent", table_name="curation_candidate")
    op.drop_index("ix_curation_candidate_tenant_id", table_name="curation_candidate")
    op.drop_table("curation_candidate")
    op.drop_index("ix_eval_dataset_agent", table_name="eval_dataset")
    op.drop_index("ix_eval_dataset_tenant_id", table_name="eval_dataset")
    op.drop_table("eval_dataset")
