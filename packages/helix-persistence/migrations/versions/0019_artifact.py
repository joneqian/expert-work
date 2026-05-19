"""Stream J.9 — agent artifacts: artifact / artifact_version tables.

Revision ID: 0019_artifact
Revises: 0018_user_workspace
Create Date: 2026-05-19

A logical ``artifact`` (a named file an agent explicitly produced) owns
one or more ``artifact_version`` revisions (STREAM-J-DESIGN § 10). The
file *content* lives in the user's J.15 persistent workspace volume;
these tables carry only metadata + the volume-relative path.

* Both tables are per-user data tables, so each lands the combined
  tenant + user RLS — ``app.tenant_id`` AND ``app.user_id`` (Mini-ADR
  J-1, as ``memory_item`` in migration ``0017``). ``artifact_version``
  denormalises ``tenant_id`` / ``user_id`` so it can carry the same
  policy; ``artifact_id`` is a bare UUID column with no FK — a FK into
  the ``FORCE`` RLS ``artifact`` table is a known footgun (Mini-ADR
  J-1a).

* ``size_bytes`` / ``sha256`` are nullable — filled lazily the first
  time the artifact's content is read (the supervisor reads the
  volume). ``save_artifact`` records the row without them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0019_artifact"
down_revision: str | Sequence[str] | None = "0018_user_workspace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

_ARTIFACT_POLICY = "artifact_isolation"
_VERSION_POLICY = "artifact_version_isolation"

#: Combined tenant + user predicate — both GUCs unset → NULLIF→NULL → deny.
_ISOLATION = (
    "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid "
    "AND user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"
)


def _enable_combined_rls(table: str, policy: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
    op.execute(f"DROP POLICY IF EXISTS {policy} ON {table};")
    op.execute(f"CREATE POLICY {policy} ON {table} USING ({_ISOLATION}) WITH CHECK ({_ISOLATION});")


def upgrade() -> None:
    op.create_table(
        "artifact",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("latest_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("tenant_id", "user_id", "name", name="artifact_identity_uniq"),
    )

    op.create_table(
        "artifact_version",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("artifact_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("path_in_workspace", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("created_in_thread", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # One row per (artifact, version); the index also serves the
        # per-artifact version listing.
        sa.UniqueConstraint("artifact_id", "version", name="artifact_version_identity_uniq"),
    )

    _enable_combined_rls("artifact", _ARTIFACT_POLICY)
    _enable_combined_rls("artifact_version", _VERSION_POLICY)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_VERSION_POLICY} ON artifact_version;")
    op.execute(f"DROP POLICY IF EXISTS {_ARTIFACT_POLICY} ON artifact;")
    op.drop_table("artifact_version")
    op.drop_table("artifact")
