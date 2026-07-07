"""8.5 — role_binding.conditions (ABAC narrowing) + platform-scope CHECK.

Adds a nullable JSONB ``conditions`` column carrying the fine-grained
:class:`expert_work.protocol.BindingConditions` (resource_ids / labels /
owner_only). NULL ⇒ unconditioned (type-wide grant, the prior behaviour).
A CHECK enforces that platform-scope bindings (cross-tenant system_admin)
never carry conditions.

Revision id ``0079_role_binding_conditions`` = 28 chars (within the 32-char
alembic ``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0079_role_binding_conditions"
down_revision: str | Sequence[str] | None = "0078_curation_evolved_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_CHECK = "role_binding_platform_no_conditions_ck"


def upgrade() -> None:
    op.add_column(
        "role_binding",
        sa.Column("conditions", JSONB, nullable=True),
    )
    op.create_check_constraint(
        _CHECK,
        "role_binding",
        "platform_scope = false OR conditions IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint(_CHECK, "role_binding", type_="check")
    op.drop_column("role_binding", "conditions")
