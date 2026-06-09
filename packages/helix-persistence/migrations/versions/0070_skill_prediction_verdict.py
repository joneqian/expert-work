"""Stream SE (SE-11) — skill_prediction_verdict table.

Revision ID: 0070_skill_pred_verdict   (23 chars; within the 32-char
``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
Revises: 0069_skill_component_type
Create Date: 2026-06-09

Lands the predict→falsify ledger borrowed from agentic-harness-engineering's
Change Manifest (see ``docs/streams/STREAM-SE-DESIGN.md`` § SE-11). The replay
(``skill_eval_result``) predicted a lift from ``baseline_score`` to
``skill_score``; after promotion the rollback monitor's rolling outcome window
gives ``observed_rate``. Each sweep that judges an ACTIVE distilled version
records how much of the predicted gain held (``realized_fraction``) + the label.

No separate prediction table: the replay-derived prediction IS the
``skill_eval_result`` row (Mini-ADR SE-A18); the LLM-generator self-prediction
source is deferred to SE-11b. Diagnostic only (SE-A19) — the verdict never
gates archive; ``decide_rollback`` remains the only down-gate.

Same NULL-tenant RLS shape + ENABLE-only (no FORCE) as ``skill_eval_result`` /
``skill_run_usage`` (0065 / 0067) so the cross-tenant rollback sweep writes as
the table OWNER while tenant sessions stay isolated
([memory:skill-curator-owner-rls-exemption]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0070_skill_pred_verdict"
down_revision: str | Sequence[str] | None = "0069_skill_component_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "skill_prediction_verdict",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "skill_id",
            UUID(as_uuid=True),
            sa.ForeignKey("skill.id", ondelete="CASCADE", name="skill_pred_verdict_skill_id_fk"),
            nullable=False,
        ),
        sa.Column("skill_version", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("predicted_delta", sa.Float(), nullable=False),
        sa.Column("realized_delta", sa.Float(), nullable=False),
        sa.Column("realized_fraction", sa.Float(), nullable=False),
        sa.Column("baseline_score", sa.Float(), nullable=False),
        sa.Column("skill_score", sa.Float(), nullable=False),
        sa.Column("observed_rate", sa.Float(), nullable=False),
        sa.Column("n_window", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("skill_version >= 1", name="skill_pred_verdict_version_positive"),
        sa.CheckConstraint("n_window >= 0", name="skill_pred_verdict_n_window_nonneg"),
        sa.CheckConstraint(
            "verdict IN ('effective', 'partially_effective', 'ineffective', 'mixed', 'harmful')",
            name="skill_pred_verdict_verdict_check",
        ),
    )
    op.create_index("ix_skill_pred_verdict_tenant_id", "skill_prediction_verdict", ["tenant_id"])
    op.create_index(
        "ix_skill_pred_verdict_version",
        "skill_prediction_verdict",
        ["tenant_id", "skill_id", "skill_version", "created_at"],
    )

    op.execute("ALTER TABLE skill_prediction_verdict ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY skill_pred_verdict_tenant_isolation ON skill_prediction_verdict "
        "USING (tenant_id IS NOT DISTINCT FROM "
        "NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id IS NOT DISTINCT FROM "
        "NULLIF(current_setting('app.tenant_id', true), '')::uuid);"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS skill_pred_verdict_tenant_isolation ON skill_prediction_verdict"
    )
    op.drop_index("ix_skill_pred_verdict_version", table_name="skill_prediction_verdict")
    op.drop_index("ix_skill_pred_verdict_tenant_id", table_name="skill_prediction_verdict")
    op.drop_table("skill_prediction_verdict")
