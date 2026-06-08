"""Stream SE (SE-8-1) — skill_promote_request + skill_evolution_kill_switch.

Revision ID: 0068_skill_promote_kill   (24 chars; within the 32-char
``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
Revises: 0067_skill_run_usage
Create Date: 2026-06-08

Lands the two tables the SE-8 admin governance surface needs (see
``docs/streams/STREAM-SE-DESIGN.md`` § SE-8):

* ``skill_promote_request`` (Mini-ADR SE-A13b) — agent_private→tenant
  visibility-promotion approval flow, orthogonal to ``skill.status``. The
  ``status='pending'`` rows are the review queue; APPROVE flips the parent
  skill's ``visibility`` to ``tenant``. A partial unique index keeps at most
  one pending request per skill.
* ``skill_evolution_kill_switch`` (Mini-ADR SE-A13c) — persistent emergency
  stop for the auto-promote pipeline. ``scope='global'`` (NULL tenant,
  system_admin only) or ``scope='tenant'`` (one per tenant). Complements the
  in-process SE-7b ``CircuitBreaker`` with a durable, cross-restart override
  that ``decide_promotion`` reads as the ``evolution_halted`` input.

Both tables use the SAME NULL-tenant RLS shape as ``skill`` /
``skill_eval_result`` / ``skill_run_usage`` (migrations 0057 / 0065 / 0067):
``tenant_id IS NOT DISTINCT FROM NULLIF(current_setting('app.tenant_id',
true), '')::uuid`` and **ENABLE-only (no FORCE)** — the SE-8 review queue +
evolution worker read cross-tenant as the table OWNER, which is exempt from
RLS only while the table is ENABLE-only (see the 0057 rationale
[memory:skill-curator-owner-rls-exemption]). ``skill_promote_request`` carries
no NULL-tenant rows (agent_private→tenant is always within a tenant), but
keeps the identical policy for ecosystem consistency; the kill-switch DOES use
NULL-tenant rows for the global scope.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0068_skill_promote_kill"
down_revision: str | Sequence[str] | None = "0067_skill_run_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    # ── skill_promote_request (SE-A13b) ──────────────────────────────────
    op.create_table(
        "skill_promote_request",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "skill_id",
            UUID(as_uuid=True),
            sa.ForeignKey("skill.id", ondelete="CASCADE", name="skill_promote_request_skill_id_fk"),
            nullable=False,
        ),
        sa.Column("skill_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("requested_by_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("requested_by_agent_name", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("decided_by_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("skill_version >= 1", name="skill_promote_request_version_positive"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'superseded')",
            name="skill_promote_request_status_check",
        ),
    )
    op.create_index(
        "uq_skill_promote_request_pending",
        "skill_promote_request",
        ["skill_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_skill_promote_request_queue",
        "skill_promote_request",
        ["tenant_id", "status", "created_at"],
    )
    op.execute("ALTER TABLE skill_promote_request ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY skill_promote_request_tenant_isolation ON skill_promote_request "
        "USING (tenant_id IS NOT DISTINCT FROM "
        "NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id IS NOT DISTINCT FROM "
        "NULLIF(current_setting('app.tenant_id', true), '')::uuid);"
    )

    # ── skill_evolution_kill_switch (SE-A13c) ────────────────────────────
    op.create_table(
        "skill_evolution_kill_switch",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("engaged", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reason", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("engaged_by_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("engaged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "(scope = 'global' AND tenant_id IS NULL) "
            "OR (scope = 'tenant' AND tenant_id IS NOT NULL)",
            name="skill_evolution_kill_switch_scope_check",
        ),
    )
    op.create_index(
        "uq_skill_evolution_kill_switch_global",
        "skill_evolution_kill_switch",
        ["scope"],
        unique=True,
        postgresql_where=sa.text("scope = 'global'"),
    )
    op.create_index(
        "uq_skill_evolution_kill_switch_tenant",
        "skill_evolution_kill_switch",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("scope = 'tenant'"),
    )
    op.execute("ALTER TABLE skill_evolution_kill_switch ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY skill_evolution_kill_switch_tenant_isolation ON skill_evolution_kill_switch "
        "USING (tenant_id IS NOT DISTINCT FROM "
        "NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id IS NOT DISTINCT FROM "
        "NULLIF(current_setting('app.tenant_id', true), '')::uuid);"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS skill_evolution_kill_switch_tenant_isolation "
        "ON skill_evolution_kill_switch"
    )
    op.drop_index("uq_skill_evolution_kill_switch_tenant", table_name="skill_evolution_kill_switch")
    op.drop_index("uq_skill_evolution_kill_switch_global", table_name="skill_evolution_kill_switch")
    op.drop_table("skill_evolution_kill_switch")

    op.execute(
        "DROP POLICY IF EXISTS skill_promote_request_tenant_isolation ON skill_promote_request"
    )
    op.drop_index("ix_skill_promote_request_queue", table_name="skill_promote_request")
    op.drop_index("uq_skill_promote_request_pending", table_name="skill_promote_request")
    op.drop_table("skill_promote_request")
