"""``tenant_user.deleted_at`` — Phase 3a soft-deactivation (purge_user).

Adds one nullable timestamp stamped by ``TenantUserStore.deactivate`` when a
user is purged. NULL = active; NON-NULL = purged. ``list_by_tenant`` excludes
these rows so a purged user never reappears in the roster; ``get`` still returns
them so re-purge stays idempotent; ``resolve`` clears the stamp so a returning
identity reactivates cleanly. Idempotent, reversible, no table locks.

Revision id ``0122_tenant_user_deleted_at`` = 27 chars (within the 32-char
alembic ``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0122_tenant_user_deleted_at"
down_revision: str | Sequence[str] | None = "0121_app_user_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "tenant_user",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_user", "deleted_at")
