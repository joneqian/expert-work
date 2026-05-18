"""``memory_item`` ORM model — Stream J.3 long-term memory.

Cross-session memory for the per-user persistent agent. Each row is one
remembered fact or episodic summary, scoped to ``(tenant_id, user_id)``
and carrying an embedding for semantic retrieval.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Index, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from helix_agent.persistence.base import Base
from helix_agent.persistence.embedding import EMBEDDING_DIM


class MemoryItemRow(Base):
    """One long-term memory — a fact or episodic summary (Stream J.3).

    Tenant-scoped *and* user-scoped: RLS (migration ``0017``) enforces
    both ``app.tenant_id`` and ``app.user_id``.
    """

    __tablename__ = "memory_item"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    source_thread_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (Index("memory_item_tenant_user_idx", "tenant_id", "user_id"),)
