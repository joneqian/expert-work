"""Stream C.5 quota engine tables — tenant_quota / token_budget_ledger / token_reservation.

Revision ID: 0006_quota_tables
Revises: 0005_rls_baseline
Create Date: 2026-05-13

Lands the three tables enumerated in STREAM-C-DESIGN § 2.4 / subsystems/16
§ 3.1:

* ``tenant_quota`` — admin-managed limits per (tenant, dimension, scope).
  M0 dimensions are ``qps`` (per second) and ``tokens_per_day`` (per
  rolling 24h budget). Scope is a JSONB document like ``{"agent": "*"}``
  or ``{"agent": "code-reviewer"}``.
* ``token_budget_ledger`` — monthly used / reserved accounting. M0
  manages a single per-month row per tenant; subsystems/16 § 5.4
  documents subagent twin-commit semantics that land later.
* ``token_reservation`` — outstanding reservation records (state
  machine: ``RESERVED`` → ``COMMITTED`` / ``RELEASED`` / ``EXPIRED``)
  used by the 30-minute reaper to refund leaked reservations and by
  the audit log for cost reconciliation.

All three tables enable Postgres ROW LEVEL SECURITY with the same
``tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid``
policy the C.4 baseline uses (idempotent — same predicate / NULLIF
handling).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_quota_tables"
down_revision: str | Sequence[str] | None = "0005_rls_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


_TENANT_TABLES: tuple[str, ...] = (
    "tenant_quota",
    "token_budget_ledger",
    "token_reservation",
)


def upgrade() -> None:
    # --- tenant_quota ----------------------------------------------------
    # Admin-curated row per (tenant, dimension, scope) triple. ``scope``
    # is JSONB so M1 can add ``model`` / ``provider`` keys without an
    # ALTER TABLE; the UNIQUE constraint hashes the JSONB serialisation
    # so an empty / star-scope row is the canonical "tenant default".
    op.create_table(
        "tenant_quota",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dimension", sa.Text(), nullable=False),
        sa.Column(
            "scope",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("limit_value", sa.BigInteger(), nullable=False),
        sa.Column("burst", sa.BigInteger(), nullable=True),
        sa.Column(
            "effective_from",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dimension",
            "scope",
            name="tenant_quota_tenant_dimension_scope_uniq",
        ),
    )
    op.create_index("tenant_quota_tenant_dim_idx", "tenant_quota", ["tenant_id", "dimension"])

    # --- token_budget_ledger --------------------------------------------
    op.create_table(
        "token_budget_ledger",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("budget_total", sa.BigInteger(), nullable=False),
        sa.Column("used_total", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("reserved_total", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", "month", name="token_budget_ledger_tenant_month_uniq"),
        # used_total + reserved_total must not exceed budget_total —
        # enforced at the application layer in commit/reserve paths;
        # not a CHECK constraint so admins can hot-patch limits.
    )

    # --- token_reservation ----------------------------------------------
    op.create_table(
        "token_reservation",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_thread_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("estimated", sa.BigInteger(), nullable=False),
        sa.Column("actual", sa.BigInteger(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column(
            "reserved_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Used by the 30-min reaper to scan RESERVED rows older than the
    # expiry threshold and force-release them.
    op.create_index(
        "token_reservation_state_reserved_at_idx",
        "token_reservation",
        ["state", "reserved_at"],
    )
    op.create_index("token_reservation_tenant_idx", "token_reservation", ["tenant_id"])

    # --- RLS on the three new tables ------------------------------------
    # Identical policy + NULLIF empty-string handling as 0005 so the
    # application sessionmaker wiring needs no per-table changes.
    for table in _TENANT_TABLES:
        policy = f"{table}_tenant_isolation"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table};")
        op.execute(
            f"""
            CREATE POLICY {policy} ON {table}
                USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
            """
        )


def downgrade() -> None:
    for table in _TENANT_TABLES:
        policy = f"{table}_tenant_isolation"
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table};")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
    op.drop_index("token_reservation_tenant_idx", table_name="token_reservation")
    op.drop_index("token_reservation_state_reserved_at_idx", table_name="token_reservation")
    op.drop_table("token_reservation")
    op.drop_table("token_budget_ledger")
    op.drop_index("tenant_quota_tenant_dim_idx", table_name="tenant_quota")
    op.drop_table("tenant_quota")
