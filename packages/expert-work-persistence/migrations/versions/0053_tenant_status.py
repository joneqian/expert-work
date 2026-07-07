"""Stream U — tenant lifecycle status column (PR E).

Adds a ``status`` column to ``tenant_config`` distinguishing ``active`` from
``suspended`` tenants. A CHECK constraint mirrors the Python ``TenantStatus``
Literal. Existing rows default to ``active`` (server_default), so the migration
is backwards compatible.

Revision id ``0053_tenant_status`` = 18 chars (within the 32-char alembic
``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053_tenant_status"
down_revision: str | Sequence[str] | None = "0051_platform_embed_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "tenant_config",
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
    )
    op.create_check_constraint(
        "ck_tenant_config_status",
        "tenant_config",
        "status IN ('active', 'suspended')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tenant_config_status", "tenant_config", type_="check")
    op.drop_column("tenant_config", "status")
