"""``user_workspace`` ORM model — Stream J.15 per-user execution environment.

One row per ``(tenant_id, user_id)`` pair, registering the docker named
volume that backs that user's persistent workspace. The volume outlives
the ephemeral sandbox containers that mount it; see migration
``0018_user_workspace`` and STREAM-J-DESIGN § 9.

J.15-补强-1 (migration 0026, STREAM-J-DESIGN § 9.5) adds three columns:

- ``size_limit_bytes`` (Mini-ADR J-29 第 1 项) — quota ceiling.
- ``deleted_at`` + ``archived_object_key`` (Mini-ADR J-36) — lifecycle
  三档 (active -> soft-deleted -> archived).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from helix_agent.persistence.base import Base


class UserWorkspaceRow(Base):
    """A user's persistent workspace volume (Stream J.15).

    Supervisor-owned, like ``sandbox_instance`` — no RLS. The
    sandbox-supervisor authenticates callers via mTLS and scopes by
    ``(tenant_id, user_id)`` in the application layer (Mini-ADR J-1).
    """

    __tablename__ = "user_workspace"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    #: docker named volume — deterministic per (tenant, user), see ``workspace.base``.
    volume_name: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    #: J.15-补强-1: per-workspace quota ceiling (Mini-ADR J-29 第 1 项).
    size_limit_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text(str(10 * 1024 * 1024 * 1024))
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: RT-6 Tier B (RT-ADR-20): bumped to now() under the workspace write lock
    #: whenever a write-capable tool (write_file / edit_file / bash) completes —
    #: a conservative over-approximation of a mutation (a read-only bash bumps
    #: too). A paused approval predating it may have drifted. NULL until the
    #: first such write after the column was added.
    last_write_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: J.15-补强-1 (Mini-ADR J-36): soft-delete timestamp. NULL ⇒ active.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: J.15-补强-1 (Mini-ADR J-36): ObjectStore key where the archived
    #: tar.zst landed. NULL while pending archive; populated by reaper.
    archived_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="user_workspace_identity_uniq"),
        CheckConstraint(
            "archived_object_key IS NULL OR deleted_at IS NOT NULL",
            name="user_workspace_archive_consistency",
        ),
        Index(
            "user_workspace_pending_archive_idx",
            "deleted_at",
            postgresql_where=text("deleted_at IS NOT NULL AND archived_object_key IS NULL"),
        ),
    )
