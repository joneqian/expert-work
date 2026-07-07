"""Stream SE (SE-7d-1) — skill_run_usage attribution table.

Revision ID: 0067_skill_run_usage   (20 chars; within the 32-char
``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
Revises: 0066_skill_owner_per_user
Create Date: 2026-06-08

Lands the **skill-centric** attribution table the regression-rollback
monitor (SE-7d-3) reads (see ``docs/streams/STREAM-SE-DESIGN.md`` § 4.4).
A run (``thread_id``) that loaded a skill's ``skill_version`` records its
``outcome`` here; the monitor aggregates per ``(skill_id, skill_version)``
over a rolling window and archives a version whose success rate regresses.

Why a dedicated table, not trajectory metadata: rollback judgment is a
skill-centric query, while trajectory storage is run-centric — pinning
the former on a full scan of the latter is a modeling mismatch that does
not hold at the SkillActivityRecorder design target (1000 runs/sec). The
``ix_skill_run_usage_window`` index turns the window aggregation into a
range scan.

``skill_run_usage`` uses the SAME NULL-tenant RLS shape as ``skill`` /
``skill_eval_result`` (migrations 0057 / 0065): ``tenant_id IS NOT
DISTINCT FROM NULLIF(current_setting('app.tenant_id', true), '')::uuid``
and **ENABLE-only (no FORCE)** — the evolution worker (SE-6/7d) reads
cross-tenant as the table OWNER, which is exempt from RLS only while the
table is ENABLE-only (see the 0057 rationale
[memory:skill-curator-owner-rls-exemption]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0067_skill_run_usage"
down_revision: str | Sequence[str] | None = "0066_skill_owner_per_user"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "skill_run_usage",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "skill_id",
            UUID(as_uuid=True),
            sa.ForeignKey("skill.id", ondelete="CASCADE", name="skill_run_usage_skill_id_fk"),
            nullable=False,
        ),
        sa.Column("skill_version", sa.Integer(), nullable=False),
        sa.Column("thread_id", UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("skill_version >= 1", name="skill_run_usage_version_positive"),
        sa.CheckConstraint(
            "outcome IN ('success', 'failed', 'max_steps', 'cancelled')",
            name="skill_run_usage_outcome_check",
        ),
    )
    op.create_index("ix_skill_run_usage_tenant_id", "skill_run_usage", ["tenant_id"])
    op.create_index(
        "ix_skill_run_usage_window",
        "skill_run_usage",
        ["tenant_id", "skill_id", "skill_version", "created_at"],
    )

    # NULL-tenant RLS, ENABLE-only (no FORCE) — mirrors ``skill_eval_result``
    # (0065) so the SE-7d rollback monitor can read cross-tenant as the table
    # owner while tenant sessions stay isolated.
    op.execute("ALTER TABLE skill_run_usage ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY skill_run_usage_tenant_isolation ON skill_run_usage "
        "USING (tenant_id IS NOT DISTINCT FROM "
        "NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id IS NOT DISTINCT FROM "
        "NULLIF(current_setting('app.tenant_id', true), '')::uuid);"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS skill_run_usage_tenant_isolation ON skill_run_usage")
    op.drop_index("ix_skill_run_usage_window", table_name="skill_run_usage")
    op.drop_index("ix_skill_run_usage_tenant_id", table_name="skill_run_usage")
    op.drop_table("skill_run_usage")
