"""Stream J.7a — ``skill`` + ``skill_version`` tables.

Revision ID: 0029_skill
Revises: 0028_image_upload
Create Date: 2026-05-21

Mini-ADR J-23 (Stream J.7a) — reusable skill bundles. Double-table model:

* ``skill`` — the named bundle with lifecycle state (``draft`` /
  ``active`` / ``archived``) + ``latest_version`` pointer for bare
  ``name`` resolutions.
* ``skill_version`` — append-only per-version row carrying the
  ``prompt_fragment`` + ``tool_names`` subset + ``description`` /
  ``category`` / ``required_models`` metadata. Pinned ``name@N``
  references resolve directly here.

Tenant RLS via ``current_setting('app.tenant_id')`` GUC, same pattern
as audit_log / memory_item / image_upload / user_workspace.

Indexes:
- ``ix_skill_tenant_id`` for tenant-scoped list / search
- ``ix_skill_status`` partial (status='active') for bare-name resolution
- ``ix_skill_version_skill_id`` for version listing
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0029_skill"
down_revision: str | Sequence[str] | None = "0028_image_upload"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "skill",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("latest_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("category", sa.Text(), nullable=True),
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
        sa.CheckConstraint("status IN ('draft', 'active', 'archived')", name="skill_status_check"),
        sa.CheckConstraint("latest_version >= 0", name="skill_latest_version_nonneg"),
        sa.UniqueConstraint("tenant_id", "name", name="skill_tenant_name_uq"),
    )
    op.create_index("ix_skill_tenant_id", "skill", ["tenant_id"])
    op.create_index(
        "ix_skill_status_active",
        "skill",
        ["tenant_id", "name"],
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "skill_version",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("skill_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("prompt_fragment", sa.Text(), nullable=False),
        sa.Column(
            "tool_names",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column(
            "required_models",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "authored_by",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'human'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("version >= 1", name="skill_version_positive"),
        sa.CheckConstraint(
            "authored_by IN ('human', 'agent')", name="skill_version_authored_by_check"
        ),
        sa.UniqueConstraint("skill_id", "version", name="skill_version_skill_version_uq"),
        sa.ForeignKeyConstraint(
            ["skill_id"], ["skill.id"], ondelete="CASCADE", name="skill_version_skill_id_fk"
        ),
    )
    op.create_index("ix_skill_version_tenant_id", "skill_version", ["tenant_id"])
    op.create_index("ix_skill_version_skill_id", "skill_version", ["skill_id"])

    # Tenant RLS — same ``app.tenant_id`` GUC pattern.
    op.execute("ALTER TABLE skill ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY skill_tenant_isolation ON skill "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute("ALTER TABLE skill_version ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY skill_version_tenant_isolation ON skill_version "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS skill_version_tenant_isolation ON skill_version")
    op.execute("DROP POLICY IF EXISTS skill_tenant_isolation ON skill")
    op.drop_index("ix_skill_version_skill_id", table_name="skill_version")
    op.drop_index("ix_skill_version_tenant_id", table_name="skill_version")
    op.drop_table("skill_version")
    op.drop_index("ix_skill_status_active", table_name="skill")
    op.drop_index("ix_skill_tenant_id", table_name="skill")
    op.drop_table("skill")
