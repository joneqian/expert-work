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

# Capability Uplift Sprint #7 — Mini-ADR U-33.
# Lifecycle of a memory item:
#   transient    — raw write from memory_writeback_node (default for new)
#   consolidated — created by MemoryConsolidator; consolidated_from holds
#                  the transient source UUIDs that this entry summarises
#   archived     — reserved for M2-C cold-storage pipeline; Sprint #7
#                  registers the state + retrieve() filter, M2-C wires
#                  the archive() implementation
MemoryStatus = Literal["transient", "consolidated", "archived"]


class MemoryItem(BaseModel):
    """One row of ``memory_item`` — a cross-session memory."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    user_id: UUID
    kind: Literal["fact", "episodic"] = Field(
        description="fact = stable preference / truth; episodic = summary of an interaction"
    )
    # Stream Agent-Templates (M1-5c) — the agent this memory belongs to. Set only
    # for ``episodic`` items (per-agent isolation: a support agent's events do not
    # leak into a sales agent's recall); ``None`` for ``fact`` items, which are
    # agent-agnostic user profile shared across all of a user's agents.
    agent_name: str | None = Field(
        default=None,
        description="owning agent for episodic memory; None = shared fact (agent-agnostic)",
    )
    content: str
    embedding: tuple[float, ...] = Field(
        repr=False, description="semantic embedding vector of ``content``"
    )
    # Stream Memory-Enhance (M-2) — importance/confidence scoring.
    # ``importance`` = how reusable this memory is likely to be in future
    # sessions (rare stable user facts high; one-off chatter low). The
    # writeback path drops items below ``LongTermMemorySpec.write_min_importance``.
    # ``confidence`` = how sure the extraction is (explicit statements high;
    # inferred / hedged low). Both 0-1, default 0.5 (neutral) so legacy rows
    # and callers that omit them are unaffected. A user self-correction
    # (M-4) sets ``confidence`` to 1.0 (user-asserted truth).
    importance: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="future-reuse value of this memory, 0-1 (write-filter input)",
    )
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="extraction confidence, 0-1 (1.0 = user-asserted via correction)",
    )
    source_thread_id: str | None = Field(
        default=None, description="the thread this memory was extracted from"
    )
    content_hash: str | None = Field(
        default=None,
        description="Stream K.K7 — SHA-256 hex of ``lower(trim(content))``. "
        "Filled by the store at write time when ``None`` so callers do "
        "not need to import the hash helper; the DB column is NOT NULL.",
    )
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    deleted_at: datetime | None = Field(
        default=None,
        description="Stream K.K6 — soft-delete timestamp (forget). "
        "When set the item is hidden from retrieve / list but kept "
        "until the retention sweep hard-deletes it.",
    )
    drift: bool = Field(
        default=False,
        description="Capability Uplift Sprint #2 (Mini-ADR U-4) — "
        "transient flag set by ``MemoryStore.retrieve()`` when "
        "``sha256(lower(trim(content)))`` does not match the stored "
        "``content_hash`` (DB-drift signal). Not persisted; defaults "
        "to ``False`` on all other paths so legacy callers are "
        "unaffected.",
    )
    # Capability Uplift Sprint #7 (Mini-ADR U-33) — lifecycle.
    status: MemoryStatus = Field(
        default="transient",
        description="Lifecycle stage. Defaults to ``transient`` so existing "
        "callers and the writeback path are unaffected. ``MemoryConsolidator`` "
        "creates ``consolidated`` entries; M2-C archive pipeline sets ``archived``.",
    )
    consolidated_into: UUID | None = Field(
        default=None,
        description="When non-NULL, this transient item has been superseded "
        "by a consolidated parent. ``MemoryStore.retrieve()`` default WHERE "
        "skips items where this is set (prevents double-counting raw + summary).",
    )
    consolidated_from: tuple[UUID, ...] = Field(
        default=(),
        description="Only populated on consolidated items. Reverse index of "
        "the transient source UUIDs that this consolidated fact summarises. "
        "Persisted as JSONB array in ``memory_item.consolidated_from``.",
    )
    last_reviewed_at: datetime | None = Field(
        default=None,
        description="Capability Uplift Sprint #7 (Mini-ADR U-37) — set by "
        "MemoryConsolidator's lone-item review sub-pass when the LLM "
        "classifies an aged lone transient as durable. NULL ↔ never "
        "reviewed; non-NULL ↔ skip re-review (prevents borderline-fact "
        "thrash where repeated LLM rolls could eventually flag-and-purge).",
    )
    review_flagged_at: datetime | None = Field(
        default=None,
        description="Stream HX-2 (Mini-ADR HX-B3) — set by the "
        "FeedbackConsumerWorker when a user 👎 lands on this item's "
        "source thread. The MemoryConsolidator's SUB-PASS 2 reviews "
        "flagged items regardless of age and clears the flag via "
        "mark_reviewed; soft-deleted noise clears implicitly.",
    )
