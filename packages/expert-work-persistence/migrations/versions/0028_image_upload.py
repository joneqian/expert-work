"""Stream J.6.补强-3 — ``image_upload`` table for image lifecycle.

Revision ID: 0028_image_upload
Revises: 0027_volume_backup_dlq
Create Date: 2026-05-21

Adds the ``image_upload`` registry that records every image landed via
``POST /v1/sessions/{thread_id}/uploads``. Required for Mini-ADR J-32
M0 image lifecycle:

* ``deleted_at IS NULL`` = active image (visible to runs, billable
  against ``IMAGE_STORAGE_BYTES`` quota).
* ``deleted_at IS NOT NULL`` = soft-deleted; the object store key
  stays until the retention sweep hard-deletes it.

A partial index on ``(deleted_at)`` makes the retention sweep + active
listing constant-time as the table grows.

Tenant RLS uses the standard ``current_setting('app.tenant_id')``
GUC pattern shared with the rest of the schema.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0028_image_upload"
down_revision: str | Sequence[str] | None = "0027_volume_backup_dlq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "image_upload",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("thread_id", UUID(as_uuid=True), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("size_bytes >= 0", name="image_upload_size_nonneg"),
    )
    op.create_index(
        "ix_image_upload_tenant_id",
        "image_upload",
        ["tenant_id"],
    )
    op.create_index(
        "ix_image_upload_thread_id",
        "image_upload",
        ["thread_id"],
    )
    # Partial index — retention sweep + active-list scans land in O(log N)
    # rather than full-table scans as the historical fact-table grows.
    op.create_index(
        "ix_image_upload_active",
        "image_upload",
        ["tenant_id", "created_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_image_upload_pending_hard_delete",
        "image_upload",
        ["deleted_at"],
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )

    # Tenant RLS — same ``app.tenant_id`` GUC pattern as audit_log,
    # memory_item, user_workspace.
    op.execute("ALTER TABLE image_upload ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY image_upload_tenant_isolation ON image_upload "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS image_upload_tenant_isolation ON image_upload")
    op.drop_index("ix_image_upload_pending_hard_delete", table_name="image_upload")
    op.drop_index("ix_image_upload_active", table_name="image_upload")
    op.drop_index("ix_image_upload_thread_id", table_name="image_upload")
    op.drop_index("ix_image_upload_tenant_id", table_name="image_upload")
    op.drop_table("image_upload")
