"""Stream SE (SE-3a) — skill owner = per-user persistent agent.

Revision ID: 0066_skill_owner_per_user   (24 chars; within the 32-char
``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
Revises: 0065_skill_evolution
Create Date: 2026-06-06

SE-1 (migration 0065) modelled the agent-private owner as a single
``created_by_agent_id UUID``. The owner identity decision for self-evolving
skills is **the per-user persistent agent** (the product north star —
[memory:project_target_product_form]): a skill an agent authors is private
to *that user's that agent*, and must stay owned across manifest version
bumps. A version-specific AgentSpec UUID is the wrong key (it would orphan
private skills on every manifest edit); the stable key is
``(tenant_id, created_by_user_id, created_by_agent_name)``.

This migration replaces the unused ``created_by_agent_id`` column with
``created_by_user_id UUID`` + ``created_by_agent_name TEXT``. Safe because
no writer/reader exists yet (SE-3b is the first) — no data to preserve.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0066_skill_owner_per_user"
down_revision: str | Sequence[str] | None = "0065_skill_evolution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.drop_column("skill", "created_by_agent_id")
    op.add_column("skill", sa.Column("created_by_user_id", UUID(as_uuid=True), nullable=True))
    op.add_column("skill", sa.Column("created_by_agent_name", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("skill", "created_by_agent_name")
    op.drop_column("skill", "created_by_user_id")
    op.add_column("skill", sa.Column("created_by_agent_id", UUID(as_uuid=True), nullable=True))
