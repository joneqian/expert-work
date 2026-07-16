"""``tenant_config.http_tool_denylist`` — E.8 HTTP-tool denylist model.

The ``http`` tool moves from a default-deny allowlist to a denylist model
mirroring the sandbox ``NetworkSpec``: an empty ``http_tool_allowlist`` now
means "allow all public hosts" (the tool's static SSRF guard still blocks
private / loopback / link-local / metadata targets) rather than deny-all. This
column lets an admin block specific hosts even under allow-all — it takes
precedence over the allowlist. Host entries match exact or subdomain.

``NOT NULL DEFAULT '[]'::jsonb`` so existing ``tenant_config`` rows backfill
silently. Idempotent, reversible, no table locks.

Revision id ``0123_http_tool_denylist`` = 23 chars (within the 32-char alembic
``version_num`` ceiling).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0123_http_tool_denylist"
down_revision: str | Sequence[str] | None = "0122_tenant_user_deleted_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "tenant_config",
        sa.Column(
            "http_tool_denylist",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_config", "http_tool_denylist")
