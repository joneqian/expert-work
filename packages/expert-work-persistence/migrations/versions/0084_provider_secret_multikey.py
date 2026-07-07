"""Stream Y-MK — per-provider multi-key for ``platform_provider_secret``.

Lets a provider hold multiple credential refs (separate billing accounts /
rate-limit pools) for failover. Adds ``key_id`` + ``priority`` and repoints the
primary key from ``(provider)`` to ``(provider, key_id)``. Existing rows
backfill ``key_id='default'`` / ``priority=100`` via the column server
defaults, so the change is in-place and non-destructive.

Tenant overrides (``tenant_provider_secret``) are intentionally left 1:1 —
tenant-level multi-key is deferred to Y-MK-2.

Revision ID: 0084_provider_secret_multikey   (29 chars; within the 32-char
``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0084_provider_secret_multikey"
down_revision: str | Sequence[str] | None = "0083_platform_billing_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PK = "platform_provider_secret_pkey"
_TABLE = "platform_provider_secret"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("key_id", sa.Text(), nullable=False, server_default=sa.text("'default'")),
    )
    op.add_column(
        _TABLE,
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("100")),
    )
    op.drop_constraint(_PK, _TABLE, type_="primary")
    op.create_primary_key(_PK, _TABLE, ["provider", "key_id"])


def downgrade() -> None:
    # Collapse to one key per provider before restoring the single-column PK
    # (otherwise duplicate providers would violate it). Keep the 'default' key.
    op.execute("DELETE FROM platform_provider_secret WHERE key_id <> 'default'")
    op.drop_constraint(_PK, _TABLE, type_="primary")
    op.create_primary_key(_PK, _TABLE, ["provider"])
    op.drop_column(_TABLE, "priority")
    op.drop_column(_TABLE, "key_id")
