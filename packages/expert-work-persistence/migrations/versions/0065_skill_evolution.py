"""Stream SE (SE-1) — self-evolving skill data model.

Revision ID: 0065_skill_evolution   (18 chars; within the 32-char
``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
Revises: 0064_tenant_mcp_health
Create Date: 2026-06-06

Lands the data model for Stream SE (self-evolving skills, see
``docs/streams/STREAM-SE-DESIGN.md`` § 4). Pure-additive: new columns +
one new table, no destructive change.

``skill`` gains ownership / lineage columns (Mini-ADR SE-A1, J.7b-1 §15.7):

* ``visibility`` (TEXT, default ``'tenant'``, CHECK agent_private|tenant) —
  agent self-authored skills go ``agent_private`` until a governance gate
  promotes them; M0 human skills keep the default ``'tenant'``.
* ``created_by_agent_id`` (UUID, nullable) — provenance of the authoring
  agent instance.
* ``forked_from`` (UUID, nullable) — fork-lineage source skill id. No FK:
  deleting a source skill must not cascade to its forks.

``skill_version`` gains evolution-provenance columns (Mini-ADR SE-A1):

* ``evolution_origin`` (TEXT, nullable, CHECK in_session|distilled) —
  NULL = human-authored; ``in_session`` = Layer A self-author; ``distilled``
  = Layer B posterior-distilled (SPARK).
* ``distilled_from_trajectory_key`` / ``distilled_from_candidate_id`` —
  point back at the real evidence the version was distilled from.
* ``evolution_round`` (INT, default 0) — co-evolve iteration (SE-6).

New table ``skill_eval_result`` (Mini-ADR SE-A2) — replay-verification
evidence: ``baseline_score`` vs ``skill_score`` over ``n_cases`` held-out
replays + ``verdict``. The auto-promote gate (SE-7) requires a
``verdict='pass'`` row before a non-high-risk skill goes active (SE-A0).

``skill_eval_result`` uses the SAME NULL-tenant RLS shape as ``skill``
(migration 0057): ``tenant_id IS NOT DISTINCT FROM NULLIF(
current_setting('app.tenant_id', true), '')::uuid`` and **ENABLE-only (no
FORCE)** — the evolution worker (SE-6) reads cross-tenant as the table
OWNER, which is exempt from RLS only while the table is ENABLE-only (see
the 0057 rationale [memory:skill-curator-owner-rls-exemption]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0065_skill_evolution"
down_revision: str | Sequence[str] | None = "0064_tenant_mcp_health"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    # ── skill: ownership / lineage (SE-A1) ───────────────────────────────
    op.add_column(
        "skill",
        sa.Column("visibility", sa.Text(), nullable=False, server_default=sa.text("'tenant'")),
    )
    op.add_column("skill", sa.Column("created_by_agent_id", UUID(as_uuid=True), nullable=True))
    op.add_column("skill", sa.Column("forked_from", UUID(as_uuid=True), nullable=True))
    op.create_check_constraint(
        "skill_visibility_check",
        "skill",
        "visibility IN ('agent_private', 'tenant')",
    )

    # ── skill_version: evolution provenance (SE-A1) ──────────────────────
    op.add_column("skill_version", sa.Column("evolution_origin", sa.Text(), nullable=True))
    op.add_column(
        "skill_version",
        sa.Column("distilled_from_trajectory_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "skill_version",
        sa.Column("distilled_from_candidate_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "skill_version",
        sa.Column("evolution_round", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_check_constraint(
        "skill_version_evolution_origin_check",
        "skill_version",
        "evolution_origin IS NULL OR evolution_origin IN ('in_session', 'distilled')",
    )
    op.create_check_constraint(
        "skill_version_evolution_round_nonneg",
        "skill_version",
        "evolution_round >= 0",
    )

    # ── skill_eval_result (SE-A2) ────────────────────────────────────────
    op.create_table(
        "skill_eval_result",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "skill_id",
            UUID(as_uuid=True),
            sa.ForeignKey("skill.id", ondelete="CASCADE", name="skill_eval_result_skill_id_fk"),
            nullable=False,
        ),
        sa.Column("skill_version", sa.Integer(), nullable=False),
        sa.Column("baseline_score", sa.Float(), nullable=False),
        sa.Column("skill_score", sa.Float(), nullable=False),
        sa.Column("delta", sa.Float(), nullable=False),
        sa.Column("n_cases", sa.Integer(), nullable=False),
        sa.Column("replay_source", sa.Text(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("high_risk", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("evolution_round", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("skill_version >= 1", name="skill_eval_result_version_positive"),
        sa.CheckConstraint("n_cases >= 0", name="skill_eval_result_n_cases_nonneg"),
        sa.CheckConstraint(
            "replay_source IN ('trajectory', 'eval_dataset')",
            name="skill_eval_result_replay_source_check",
        ),
        sa.CheckConstraint(
            "verdict IN ('pass', 'fail', 'inconclusive')",
            name="skill_eval_result_verdict_check",
        ),
    )
    op.create_index("ix_skill_eval_result_tenant_id", "skill_eval_result", ["tenant_id"])
    op.create_index(
        "ix_skill_eval_result_skill", "skill_eval_result", ["skill_id", "skill_version"]
    )

    # NULL-tenant RLS, ENABLE-only (no FORCE) — mirrors ``skill`` (0057) so
    # platform-skill eval results live as NULL-tenant rows and the SE-6
    # evolution worker can read cross-tenant as the table owner.
    op.execute("ALTER TABLE skill_eval_result ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY skill_eval_result_tenant_isolation ON skill_eval_result "
        "USING (tenant_id IS NOT DISTINCT FROM "
        "NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id IS NOT DISTINCT FROM "
        "NULLIF(current_setting('app.tenant_id', true), '')::uuid);"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS skill_eval_result_tenant_isolation ON skill_eval_result")
    op.drop_index("ix_skill_eval_result_skill", table_name="skill_eval_result")
    op.drop_index("ix_skill_eval_result_tenant_id", table_name="skill_eval_result")
    op.drop_table("skill_eval_result")

    op.drop_constraint("skill_version_evolution_round_nonneg", "skill_version", type_="check")
    op.drop_constraint("skill_version_evolution_origin_check", "skill_version", type_="check")
    op.drop_column("skill_version", "evolution_round")
    op.drop_column("skill_version", "distilled_from_candidate_id")
    op.drop_column("skill_version", "distilled_from_trajectory_key")
    op.drop_column("skill_version", "evolution_origin")

    op.drop_constraint("skill_visibility_check", "skill", type_="check")
    op.drop_column("skill", "forked_from")
    op.drop_column("skill", "created_by_agent_id")
    op.drop_column("skill", "visibility")
