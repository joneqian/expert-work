"""Webhook channel formats — ``webhook_endpoint.payload_format``.

Delivery body shape per endpoint: ``generic`` (the signed helix envelope,
backfilled onto every existing row via the server default) or an IM
incoming-webhook bot message (``feishu`` / ``dingtalk`` / ``wecom``) so
approval / skill-promote events reach humans without a receiver service.
Plain Text, no CHECK — the protocol-side ``WebhookPayloadFormat`` Literal
is the value registry (same convention as ``token_usage.usage_kind``).

Revision ID: 0112_webhook_payload_format
Revises: 0111_backfill_skill_hash
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0112_webhook_payload_format"
down_revision: str | Sequence[str] | None = "0111_backfill_skill_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "webhook_endpoint",
        sa.Column(
            "payload_format",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'generic'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("webhook_endpoint", "payload_format")
