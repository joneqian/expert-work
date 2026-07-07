"""HX-9 — ``webhook_endpoint`` + ``webhook_delivery`` tables (STREAM-HX § 13).

Revision ID: 0074_webhook_hook
Revises: 0073_tenant_secrets
Create Date: 2026-06-13

HX-9 租户级出站 webhook hook — the outbound dual of the J.10 inbound
triggers. Two tables:

* ``webhook_endpoint`` — a registered delivery target (tenant URL +
  subscribed ``event_types`` + hashed HMAC secret). A partial index over
  ``enabled = true`` keeps the worker's endpoint-match scan cheap. An
  endpoint name is unique per tenant.
* ``webhook_delivery`` — one event→endpoint delivery. ``(endpoint_id,
  event_id)`` is unique so re-scanning the event spine enqueues
  idempotently; the DLQ retry state (``attempt`` / ``next_retry_at`` /
  ``status``) lives here. A partial index over the deliverable statuses
  keeps the delivery sweep cheap.

Both tables use the standard ``current_setting('app.tenant_id')`` GUC
tenant RLS.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0074_webhook_hook"
down_revision: str | Sequence[str] | None = "0073_tenant_secrets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_SOURCE_VALUES = "('manifest', 'api')"
_DELIVERY_STATUS_VALUES = "('pending', 'delivered', 'failed', 'retrying', 'dead_letter')"


def upgrade() -> None:
    op.create_table(
        "webhook_endpoint",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("event_types", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("agent_name", sa.Text(), nullable=True),
        sa.Column("secret_hash", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'api'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"source IN {_SOURCE_VALUES}", name="webhook_endpoint_source_valid"),
        sa.UniqueConstraint("tenant_id", "name", name="webhook_endpoint_name_uniq"),
    )
    op.create_index("ix_webhook_endpoint_tenant_id", "webhook_endpoint", ["tenant_id"])
    # Partial index — the delivery worker matches only enabled endpoints.
    op.create_index(
        "ix_webhook_endpoint_enabled",
        "webhook_endpoint",
        ["enabled"],
        postgresql_where=sa.text("enabled = true"),
    )

    op.create_table(
        "webhook_delivery",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("endpoint_id", UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"status IN {_DELIVERY_STATUS_VALUES}", name="webhook_delivery_status_valid"
        ),
        # Idempotent enqueue — re-scanning the event spine never double-delivers.
        sa.UniqueConstraint("endpoint_id", "event_id", name="webhook_delivery_dedup"),
    )
    op.create_index("ix_webhook_delivery_tenant_id", "webhook_delivery", ["tenant_id"])
    op.create_index("ix_webhook_delivery_endpoint_id", "webhook_delivery", ["endpoint_id"])
    # Partial index — the delivery sweep scans only deliverable rows.
    op.create_index(
        "ix_webhook_delivery_ready",
        "webhook_delivery",
        ["next_retry_at"],
        postgresql_where=sa.text("status IN ('pending', 'retrying')"),
    )

    # Tenant RLS — same ``app.tenant_id`` GUC pattern as agent_trigger / artifact.
    for table in ("webhook_endpoint", "webhook_delivery"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in ("webhook_delivery", "webhook_endpoint"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.drop_index("ix_webhook_delivery_ready", table_name="webhook_delivery")
    op.drop_index("ix_webhook_delivery_endpoint_id", table_name="webhook_delivery")
    op.drop_index("ix_webhook_delivery_tenant_id", table_name="webhook_delivery")
    op.drop_table("webhook_delivery")
    op.drop_index("ix_webhook_endpoint_enabled", table_name="webhook_endpoint")
    op.drop_index("ix_webhook_endpoint_tenant_id", table_name="webhook_endpoint")
    op.drop_table("webhook_endpoint")
