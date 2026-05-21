"""Stream J.9-step1 — artifact lifecycle columns.

Revision ID: 0030_artifact_lifecycle
Revises: 0029_skill
Create Date: 2026-05-21

Adds the data layer for the J.9 收尾 lifecycle 三档 — Mini-ADR J-25
(2026-05-20 修订 + 2026-05-21 设计修订, STREAM-J-DESIGN § 10.6).

Mirrors the J.15 ``user_workspace`` lifecycle range (migration 0026 +
Mini-ADR J-36) so the cleanup paths share one mental model.

* ``deleted_at`` — Mini-ADR J-25 (artifact soft-delete + retention).
  ``deleted_at IS NULL`` is the active state. Per-name soft-delete
  hides the entire logical artifact (all versions) from listing /
  download. The retention reaper soft-deletes active rows past
  ``artifact_retention_days`` and hard-deletes soft-deleted rows past
  the hard-delete horizon.

* ``archived_object_key`` — reserved for the M0 archive 中间档
  (tar.zst the workspace files into ObjectStore before hard-delete).
  Wired through to the column + the CHECK + the partial reaper index
  so the schema is final. The actual supervisor-side archive flow
  ships in a follow-up step that reuses the J.15 volume archive path
  (Mini-ADR J-29 第 2 项); for J.9-step1 the reaper goes
  active → soft → hard directly and ``archived_object_key`` stays
  NULL.

* The partial index on ``(deleted_at)`` makes the reaper's
  ``list_pending_archive`` / ``list_expired`` scans constant-time as
  the active table grows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_artifact_lifecycle"
down_revision: str | Sequence[str] | None = "0029_skill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column(
        "artifact",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "artifact",
        sa.Column("archived_object_key", sa.Text(), nullable=True),
    )
    # "Archived implies deleted" — same invariant as user_workspace
    # (migration 0026). An archived row must already be soft-deleted.
    op.create_check_constraint(
        "artifact_archive_consistency",
        "artifact",
        "archived_object_key IS NULL OR deleted_at IS NOT NULL",
    )
    # Partial index for the reaper's pending-archive sweep.
    op.create_index(
        "artifact_pending_archive_idx",
        "artifact",
        ["deleted_at"],
        postgresql_where=sa.text("deleted_at IS NOT NULL AND archived_object_key IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("artifact_pending_archive_idx", table_name="artifact")
    op.drop_constraint("artifact_archive_consistency", "artifact", type_="check")
    op.drop_column("artifact", "archived_object_key")
    op.drop_column("artifact", "deleted_at")
