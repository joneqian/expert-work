"""HX-9 PR3a — rename ``webhook_endpoint.secret_hash`` → ``secret_ref``.

Revision ID: 0075_webhook_secret_ref
Revises: 0074_webhook_hook
Create Date: 2026-06-13

PR1 (0074) stored a one-way ``secret_hash`` (SHA-256), copying the J.10
inbound-trigger pattern where the platform only *verifies* a presented
token. But HX-9 is an *outbound* signer: the delivery worker must compute
HMAC-SHA256 over the request body, which needs the plaintext secret — a
hash cannot sign. The corrected design (research § 4) stores the secret in
the ``SecretStore`` (encrypted at rest) and keeps only a ``secret_ref``
pointer on the row.

The feature is not yet live (no enqueue path, no real rows), so a plain
column rename is safe — there is no hash data to migrate.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0075_webhook_secret_ref"
down_revision: str | Sequence[str] | None = "0074_webhook_hook"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.alter_column(
        "webhook_endpoint",
        "secret_hash",
        new_column_name="secret_ref",
        existing_type=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "webhook_endpoint",
        "secret_ref",
        new_column_name="secret_hash",
        existing_type=sa.Text(),
        existing_nullable=True,
    )
