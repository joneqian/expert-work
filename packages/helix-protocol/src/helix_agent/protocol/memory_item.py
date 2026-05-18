"""Long-term memory item — Stream J.3.

A :class:`MemoryItem` is one cross-session memory for a per-user
persistent agent — a stable ``fact`` or an ``episodic`` summary of a
past interaction — carrying an embedding for semantic retrieval.
Scoped to ``(tenant_id, user_id)``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MemoryItem(BaseModel):
    """One row of ``memory_item`` — a cross-session memory."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    user_id: UUID
    kind: Literal["fact", "episodic"] = Field(
        description="fact = stable preference / truth; episodic = summary of an interaction"
    )
    content: str
    embedding: tuple[float, ...] = Field(
        repr=False, description="semantic embedding vector of ``content``"
    )
    source_thread_id: str | None = Field(
        default=None, description="the thread this memory was extracted from"
    )
    created_at: datetime | None = None
    last_used_at: datetime | None = None
