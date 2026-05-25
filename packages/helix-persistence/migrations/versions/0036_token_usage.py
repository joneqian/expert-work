"""Stream G.9 — per-LLM-call token usage table.

Revision ID: 0036_token_usage
Revises: 0035_role_binding_platform_scope
Create Date: 2026-05-25

G.9 (advanced from M1-D to M0 per the 2026-05-20 未交付项 audit):
without per-tenant per-agent token visibility, the M0→M1 Gate cannot
do cost evaluation after dogfood goes live ([memory:complete-not-minimal]).

The table is **append-only** (one row per LLM call) and complements the
C.5 ``token_budget_ledger`` (one row per ``(tenant, month)`` rollup —
used for budget enforcement, not analytics). The two stay independent
for M0:

* ``token_usage`` — fine-grained event log; per-tenant/agent/model/turn
  observability. Powers Prometheus ``helix_llm_token_usage_total`` +
  Grafana per-agent dashboards.
* ``token_budget_ledger`` — coarse monthly counters; powers the C.5
  budget-enforcement fast path.

M1-D will revisit whether to fold the ledger into roll-up queries over
``token_usage`` (TBD — Mini-ADR G-9 left both options open until
operational data lands).

Schema:

* ``id`` — surrogate PK; rows are not addressed individually outside
  diagnostics.
* ``tenant_id`` — RLS scope (canonical policy).
* ``agent_name`` / ``agent_version`` — natural-key dimensions for
  per-agent rollups + Grafana variables.
* ``model`` — provider model id (eg. ``claude-sonnet-4-6``); per-model
  cost varies, so this is a first-class dimension.
* ``trace_id`` — correlates the row to the W3C trace; **no FK** (trace
  archive lives outside the DB).
* ``input_tokens`` / ``output_tokens`` — base counters.
* ``cache_creation_tokens`` / ``cache_read_tokens`` — L1 Anthropic
  prompt-cache counters; non-Anthropic models keep these at 0.
* ``observed_at`` — server-side now() so the row's clock is the
  control-plane's, not the agent's.

Indexes target the two query shapes the dashboards + budget queries
hit: per-tenant time range, and per-tenant/agent/model time range.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0036_token_usage"
down_revision: str | Sequence[str] | None = "0035_role_binding_platform_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_POLICY = "token_usage_tenant_isolation"


def upgrade() -> None:
    op.create_table(
        "token_usage",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("agent_version", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "cache_creation_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "cache_read_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "token_usage_tenant_time_idx",
        "token_usage",
        ["tenant_id", sa.text("observed_at DESC")],
    )
    op.create_index(
        "token_usage_tenant_agent_model_time_idx",
        "token_usage",
        ["tenant_id", "agent_name", "model", sa.text("observed_at DESC")],
    )

    op.execute("ALTER TABLE token_usage ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE token_usage FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON token_usage;")
    op.execute(
        f"""
        CREATE POLICY {_POLICY} ON token_usage
            USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON token_usage;")
    op.drop_index("token_usage_tenant_agent_model_time_idx", table_name="token_usage")
    op.drop_index("token_usage_tenant_time_idx", table_name="token_usage")
    op.drop_table("token_usage")
