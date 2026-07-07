"""DR metadata tables — backup_record + dr_drill.

Revision ID: 0002_dr_metadata
Revises: 0001_initial_state_layer
Create Date: 2026-05-12

Implements Stream A.6 batch 1 per subsystems/22-disaster-recovery § 3.2.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_dr_metadata"
down_revision: str | Sequence[str] | None = "0001_initial_state_layer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "backup_record",
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column("asset_type", sa.Text(), nullable=False),
        sa.Column("asset_ref", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("tier", sa.SmallInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_type", "asset_ref", name="backup_record_asset_unique"),
    )
    op.create_index(
        "backup_record_asset_time_idx",
        "backup_record",
        ["asset_type", "started_at"],
    )

    op.create_table(
        "dr_drill",
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column("drill_type", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rpo_actual_s", sa.Integer(), nullable=True),
        sa.Column("rto_actual_s", sa.Integer(), nullable=True),
        sa.Column("target_rpo_s", sa.Integer(), nullable=False),
        sa.Column("target_rto_s", sa.Integer(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("dr_drill")
    op.drop_index("backup_record_asset_time_idx", table_name="backup_record")
    op.drop_table("backup_record")
