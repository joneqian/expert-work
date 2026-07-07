"""Stream T — platform embedding/rerank config table (PR B).

Adds a single-row (``id == "singleton"``), platform-global, tenant-less table
storing the platform's chosen embedding / rerank provider+model selection.
This is **non-secret** config (provider/model names only); the actual API keys
live in ``platform_provider_secret``. An absent row means "not configured".

No RLS policy: these rows have no tenant, exactly like ``platform_provider_secret``
/ ``role_binding`` platform-scope rows — all access goes through
``bypass_rls_session()``.

Revision id ``0051_platform_embed_config`` = 26 chars (within the 32-char
alembic ``version_num`` ceiling per [memory:alembic-revision-id-32-chars]).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0051_platform_embed_config"
down_revision: str | Sequence[str] | None = "0052_tenant_default_agent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "platform_embedding_config",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("embedding_provider", sa.Text(), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("rerank_provider", sa.Text(), nullable=True),
        sa.Column("rerank_model", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("platform_embedding_config")
