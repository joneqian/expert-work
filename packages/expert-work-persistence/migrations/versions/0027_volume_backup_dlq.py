"""Stream J.15-补强-2 — volume_backup_dlq table.

Revision ID: 0027_volume_backup_dlq
Revises: 0026_workspace_quota_lifecycle
Create Date: 2026-05-21

Adds the dead-letter queue for the J.15 volume backup + archive pipeline
(STREAM-J-DESIGN § 9.5.2 / § 9.5.4 / Mini-ADR J-29 第 2 项 / J-36).

Backup success records reuse the existing ``backup_record`` table
(migration 0002 — generic ``asset_type``/``asset_ref`` schema fits
``user_workspace_backup`` / ``user_workspace_archive`` asset types).
This migration only adds the failure-retry table, modeled after K7's
``memory_writeback_dlq`` (migration 0025) — same backoff schedule, same
``attempts`` + ``next_retry_at`` + ``last_error`` shape, same partial
index for the ready-rows scan.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0027_volume_backup_dlq"
down_revision: str | Sequence[str] | None = "0026_workspace_quota_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "volume_backup_dlq",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
        sa.Column("volume_name", sa.Text(), nullable=False),
        # "archive" (J-36 third state) or "backup" (J-29 第 2 项 daily snapshot).
        # Same retry shape; different target ObjectStore prefix at worker side.
        sa.Column("op_kind", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("op_kind IN ('archive', 'backup')", name="volume_backup_dlq_op_kind"),
    )
    # Partial index — DLQ worker scans ``WHERE next_retry_at <= now()``;
    # rows that have hit MAX_ATTEMPTS are pushed far into the future
    # (365 d) so they drop off the hot index naturally.
    op.create_index(
        "volume_backup_dlq_ready_idx",
        "volume_backup_dlq",
        ["next_retry_at"],
    )


def downgrade() -> None:
    op.drop_index("volume_backup_dlq_ready_idx", table_name="volume_backup_dlq")
    op.drop_table("volume_backup_dlq")
