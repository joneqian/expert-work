"""``agent_spec`` registry table.

Revision ID: 0003_agent_spec
Revises: 0002_dr_metadata
Create Date: 2026-05-12

Implements Stream B.5 per STREAM-B-DESIGN § 2.4. Stores one row per
``(tenant_id, name, version)`` manifest. Soft-delete via ``status``
keeps audit history without breaking foreign-key style references in
session rows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_agent_spec"
down_revision: str | Sequence[str] | None = "0002_dr_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "agent_spec",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("spec_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("spec_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "tenant_id", "name", "version", name="agent_spec_tenant_name_version_uniq"
        ),
    )
    op.create_index(
        "agent_spec_tenant_status_name_idx",
        "agent_spec",
        ["tenant_id", "status", "name"],
    )


def downgrade() -> None:
    op.drop_index("agent_spec_tenant_status_name_idx", table_name="agent_spec")
    op.drop_table("agent_spec")
