"""``agent_spec_revision`` — manifest revision history (Stream HX-5).

Immutable append-only snapshots of every manifest create / content-
changing update (Mini-ADR HX-E1/E2 — the row-history table the B.5
``update_spec`` comment promised for M1). Backfills one revision per
existing ``agent_spec`` row so the *current* state of every manifest is
rollback-able from day one (without it, the first post-migration edit
would have no "before" snapshot).

RLS mirrors ``agent_spec`` (0005 baseline): ENABLE + tenant-equality
policy, no FORCE — the table-owner exemption stays available for
platform sweeps, same trade-off as the parent table.

Revision ID: 0072_agent_spec_revision
Revises: 0071_hx2_feedback_loop
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0072_agent_spec_revision"
down_revision: str = "0071_hx2_feedback_loop"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "agent_spec_revision",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("agent_version", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("spec_json", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("spec_sha256", sa.CHAR(64), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "agent_name",
            "agent_version",
            "revision",
            name="agent_spec_revision_uniq",
        ),
    )

    op.execute("ALTER TABLE agent_spec_revision ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY agent_spec_revision_tenant_isolation ON agent_spec_revision "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )

    # Backfill: the current content of every live manifest becomes
    # revision 1 (actor = original creator; timestamp = last update).
    op.execute(
        """
        INSERT INTO agent_spec_revision
            (tenant_id, agent_name, agent_version, revision,
             spec_json, spec_sha256, actor_id, created_at)
        SELECT tenant_id, name, version, 1,
               spec_json, spec_sha256, created_by, updated_at
        FROM agent_spec
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS agent_spec_revision_tenant_isolation ON agent_spec_revision")
    op.drop_table("agent_spec_revision")
