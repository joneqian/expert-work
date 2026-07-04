"""Stream RT-4 (RT-ADR-16) — ``agent_disable`` agent-level kill switch.

The per-(tenant, agent_name) emergency-stop flag. Complements the existing
tenant-level ``tenant_config.status`` suspend (migration 0053): where suspend
halts a whole tenant, this halts one agent (all its versions) — reject new
runs, bulk-cancel in-flight, and refuse to claim queued runs — reversibly.

Not folded into ``AgentSpecStatus`` (RT-ADR-16): disable is a reversible
emergency operation orthogonal to the deprecated/deleted lifecycle, and agents
are stored per ``(name, version)`` while the kill switch must cover a name's
whole version set — hence an independent table keyed on
``(tenant_id, agent_name)``.

Tenant RLS uses the standard ``app.tenant_id`` GUC pattern (same shape as
``agent_run`` / migration 0032); every row is tenant-scoped (no NULL-tenant
rows) and a tenant admin flips the switch within their own scope.

Revision id ``0114_agent_disable`` = 19 chars (within the 32-char alembic
``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0114_agent_disable"
down_revision: str | Sequence[str] | None = "0113_skill_lazy_load_default"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "agent_disable",
        sa.Column("tenant_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_name", sa.Text(), primary_key=True),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("disabled_by", sa.Text(), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Tenant RLS — same ``app.tenant_id`` GUC pattern as agent_run.
    op.execute("ALTER TABLE agent_disable ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY agent_disable_tenant_isolation ON agent_disable "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS agent_disable_tenant_isolation ON agent_disable")
    op.drop_table("agent_disable")
