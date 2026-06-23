"""模型定价简化: drop plan_tier/temporal/markup, rename price cols to per-mtok.

Simplifies ``model_rate_card`` to one cost price per ``(provider, model)``:

* DROP ``markup_bps`` (markup moves to tenant scope, separate PR),
  ``plan_tier``, ``effective_from``, ``effective_until`` (their CHECKs drop with
  the columns).
* RENAME the four price columns ``*_token_micros`` → ``*_per_mtok_micros`` —
  the unit changes from micro-元/token to micro-元/百万tokens.
* Replace the natural-key unique index with ``(tenant_id, provider, model)``.

NOTE: not value-safe for pre-existing rows — the price unit changes by 1e6 and
the temporal/tier columns are lost. Assumes no production pricing data (dev).
downgrade re-adds the dropped columns with defaults (historical values are
unrecoverable).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0089_model_pricing_simplify"
down_revision: str | Sequence[str] | None = "0088_egress_audit_null_tenant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_TABLE = "model_rate_card"
_INDEX = "model_rate_card_natural_key_uniq"
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"

_PRICE_RENAMES = (
    ("input_token_micros", "input_per_mtok_micros"),
    ("output_token_micros", "output_per_mtok_micros"),
    ("cache_creation_token_micros", "cache_creation_per_mtok_micros"),
    ("cache_read_token_micros", "cache_read_per_mtok_micros"),
)


def upgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    # Dropping a column drops the CHECK constraints that depend on it.
    op.drop_column(_TABLE, "markup_bps")
    op.drop_column(_TABLE, "plan_tier")
    op.drop_column(_TABLE, "effective_from")
    op.drop_column(_TABLE, "effective_until")
    for old, new in _PRICE_RENAMES:
        op.alter_column(_TABLE, old, new_column_name=new)
    # One price per (tenant, provider, model). COALESCE collapses platform NULL
    # tenant rows to the zero-uuid so a future per-tenant row can coexist.
    op.execute(
        f"CREATE UNIQUE INDEX {_INDEX} ON {_TABLE} "
        f"(COALESCE(tenant_id, '{_ZERO_UUID}'::uuid), provider, model)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    for old, new in _PRICE_RENAMES:
        op.alter_column(_TABLE, new, new_column_name=old)
    op.add_column(
        _TABLE,
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "effective_from",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(_TABLE, sa.Column("plan_tier", sa.Text(), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column("markup_bps", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_check_constraint("model_rate_card_markup_bps_check", _TABLE, "markup_bps >= 0")
    op.create_check_constraint(
        "model_rate_card_plan_tier_check",
        _TABLE,
        "plan_tier IS NULL OR plan_tier IN ('free', 'pro', 'enterprise')",
    )
    op.create_check_constraint(
        "model_rate_card_window_check",
        _TABLE,
        "effective_until IS NULL OR effective_until > effective_from",
    )
    op.execute(
        f"CREATE UNIQUE INDEX {_INDEX} ON {_TABLE} "
        f"(COALESCE(tenant_id, '{_ZERO_UUID}'::uuid), provider, model, "
        "COALESCE(plan_tier, ''), effective_from)"
    )
