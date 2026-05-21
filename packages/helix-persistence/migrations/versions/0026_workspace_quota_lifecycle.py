"""Stream J.15-补强-1 — user_workspace quota + lifecycle columns.

Revision ID: 0026_user_workspace_quota_lifecycle
Revises: 0025_memory_dedup_and_dlq
Create Date: 2026-05-20

Adds the data layer for two M0 (c) gaps identified in the J.15 design PR
(STREAM-J-DESIGN § 9.5):

* ``size_limit_bytes`` — Mini-ADR J-29 第 1 项 (volume quota 准入).
  Per-workspace ceiling on the docker volume's measured size. Default
  10 GiB; manifests can override via ``policies.workspace_size_limit_mb``.
  Existing rows are backfilled with the default so the column is
  ``NOT NULL`` from day one.

* ``deleted_at`` + ``archived_object_key`` — Mini-ADR J-36 (volume
  lifecycle 三档). ``deleted_at IS NULL`` is the active state.
  ``deleted_at IS NOT NULL AND archived_object_key IS NULL`` is the
  pending-archive state — reaper picks these up, tars the volume into
  ObjectStore, then sets ``archived_object_key``. A CHECK constraint
  enforces "you can't be archived without being deleted first".

A partial index on ``(deleted_at)`` makes the reaper's
``list_pending_archive`` scan a constant-time op as the active table
grows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_workspace_quota_lifecycle"
down_revision: str | Sequence[str] | None = "0025_memory_dedup_and_dlq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

# 10 GiB in bytes — must match SandboxSupervisorSettings.default_workspace_size_limit_mb x 1 MiB.
_DEFAULT_SIZE_LIMIT_BYTES = 10 * 1024 * 1024 * 1024


def upgrade() -> None:
    op.add_column(
        "user_workspace",
        sa.Column(
            "size_limit_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text(str(_DEFAULT_SIZE_LIMIT_BYTES)),
        ),
    )
    op.add_column(
        "user_workspace",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_workspace",
        sa.Column("archived_object_key", sa.Text(), nullable=True),
    )
    # "Archived implies deleted" — can't have an archived_object_key on
    # an active row (Mini-ADR J-36 三档 invariant).
    op.create_check_constraint(
        "user_workspace_archive_consistency",
        "user_workspace",
        "archived_object_key IS NULL OR deleted_at IS NOT NULL",
    )
    # Partial index for the reaper's pending-archive sweep: scans
    # constant-time rows regardless of active table size.
    op.create_index(
        "user_workspace_pending_archive_idx",
        "user_workspace",
        ["deleted_at"],
        postgresql_where=sa.text("deleted_at IS NOT NULL AND archived_object_key IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("user_workspace_pending_archive_idx", table_name="user_workspace")
    op.drop_constraint("user_workspace_archive_consistency", "user_workspace", type_="check")
    op.drop_column("user_workspace", "archived_object_key")
    op.drop_column("user_workspace", "deleted_at")
    op.drop_column("user_workspace", "size_limit_bytes")
