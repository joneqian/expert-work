"""tenant_billing_ledger derived per-tenant monthly billing buckets — Stream Y (Y-4).

Derived cost table: one row per ``(tenant_id, month, provider, model, agent_name)``
bucket, produced by the Y4 rollup job. Cost is stored as the base / markup /
billed split (integer micro-USD); tenants see only ``billed`` (Stream Z), the
split is retained for system_admin chargeback.

**Tenant-scoped** (per-tenant rows) — uses the standard strict tenant-isolation
RLS policy (matching ``token_usage``), NOT the NULL-tenant ``IS NOT DISTINCT
FROM`` catalog policy. This is a separate table from the C.5
``token_budget_ledger`` (different semantics: budget counters vs derived cost).

Revision ID: 0060_tenant_billing_ledger
Revises: 0059_model_rate_card
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0060_tenant_billing_ledger"
down_revision: str | Sequence[str] | None = "0059_model_rate_card"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "tenant_billing_ledger"
_POLICY = "tenant_billing_ledger_tenant_isolation"
_BUCKET = "tenant_billing_ledger_bucket_uniq"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=False),
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
        sa.Column("base_cost_micros", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "markup_cost_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "billed_cost_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("priced", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("rate_card_priced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("input_tokens >= 0", name="tenant_billing_ledger_input_tokens_check"),
        sa.CheckConstraint("output_tokens >= 0", name="tenant_billing_ledger_output_tokens_check"),
        sa.CheckConstraint(
            "cache_creation_tokens >= 0",
            name="tenant_billing_ledger_cache_creation_check",
        ),
        sa.CheckConstraint(
            "cache_read_tokens >= 0",
            name="tenant_billing_ledger_cache_read_check",
        ),
        sa.CheckConstraint("base_cost_micros >= 0", name="tenant_billing_ledger_base_micros_check"),
        sa.CheckConstraint(
            "markup_cost_micros >= 0",
            name="tenant_billing_ledger_markup_micros_check",
        ),
        sa.CheckConstraint(
            "billed_cost_micros >= 0",
            name="tenant_billing_ledger_billed_micros_check",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "month",
            "provider",
            "model",
            "agent_name",
            name=_BUCKET,
        ),
    )
    op.create_index(
        "tenant_billing_ledger_tenant_month_idx",
        _TABLE,
        ["tenant_id", "month"],
    )

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE};")
    op.execute(
        f"""
        CREATE POLICY {_POLICY} ON {_TABLE}
            USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE};")
    op.drop_index("tenant_billing_ledger_tenant_month_idx", table_name=_TABLE)
    op.drop_table(_TABLE)
