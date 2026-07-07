"""model_rate_card platform-curated table + NULL-tenant RLS + token_usage.provider — Stream Y (Y-3).

Platform-curated model rate card: per-``(provider, model, plan_tier)`` token
prices in integer micro-USD + a basis-point markup, temporally versioned. Mirrors
the ``0055_mcp_connector_catalog`` NULL-tenant pattern: ``tenant_id`` is NULLABLE
(NULL = platform-global, the only shape today; the column is kept so future
per-tenant private rate cards are a non-migration change). RLS uses an
``IS NOT DISTINCT FROM`` policy so future tenant rows are isolated while NULL
(platform) rows remain reachable under the bypass path.

Also adds an additive ``token_usage.provider`` column (nullable, no backfill) so
Stream Y4 can price usage by ``(provider, model)``.

Revision ID: 0059_model_rate_card
Revises: 0058_credentials_platform_only
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0059_model_rate_card"
down_revision: str | Sequence[str] | None = "0058_credentials_platform_only"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "model_rate_card"
_POLICY = "model_rate_card_tenant_isolation"
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input_token_micros", sa.BigInteger(), nullable=False),
        sa.Column("output_token_micros", sa.BigInteger(), nullable=False),
        sa.Column(
            "cache_creation_token_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "cache_read_token_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("markup_bps", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("plan_tier", sa.Text(), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("input_token_micros >= 0", name="model_rate_card_input_micros_check"),
        sa.CheckConstraint("output_token_micros >= 0", name="model_rate_card_output_micros_check"),
        sa.CheckConstraint(
            "cache_creation_token_micros >= 0",
            name="model_rate_card_cache_creation_micros_check",
        ),
        sa.CheckConstraint(
            "cache_read_token_micros >= 0",
            name="model_rate_card_cache_read_micros_check",
        ),
        sa.CheckConstraint("markup_bps >= 0", name="model_rate_card_markup_bps_check"),
        sa.CheckConstraint(
            "plan_tier IS NULL OR plan_tier IN ('free', 'pro', 'enterprise')",
            name="model_rate_card_plan_tier_check",
        ),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="model_rate_card_window_check",
        ),
    )
    # One price per (tenant, provider, model, plan_tier, effective_from).
    # COALESCE collapses NULLs (otherwise distinct in a unique index) so generic
    # plan_tier rows collide, AND — mirroring 0055 — ``tenant_id`` is the first
    # key (zero-uuid for platform NULL rows) so the "future per-tenant rate cards
    # are a non-migration change" promise holds: a tenant row can share a
    # (provider, model, tier, effective_from) with a platform row without
    # colliding.
    op.execute(
        f"CREATE UNIQUE INDEX model_rate_card_natural_key_uniq ON {_TABLE} "
        f"(COALESCE(tenant_id, '{_ZERO_UUID}'::uuid), provider, model, "
        "COALESCE(plan_tier, ''), effective_from)"
    )
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"CREATE POLICY {_POLICY} ON {_TABLE} "
        "USING (tenant_id IS NOT DISTINCT FROM "
        "NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id IS NOT DISTINCT FROM "
        "NULLIF(current_setting('app.tenant_id', true), '')::uuid);"
    )

    # Additive: Stream Y4 prices usage by (provider, model). Nullable, no backfill.
    op.add_column("token_usage", sa.Column("provider", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("token_usage", "provider")
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE};")
    op.drop_table(_TABLE)
