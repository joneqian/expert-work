"""Stream CM-6 — MMR diversity selection at the end of memory recall.

``memory_recall_node`` runs greedy MMR (λ=0.7) as the last re-ranking
stage: after the optional CM-4 cross-encoder rerank, before redaction.
Near-duplicate candidates are deduplicated in the final ``top_k``;
failures degrade to the input order (Mini-ADR CM-G6).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from expert_work.protocol import MemoryItem
from orchestrator.graph_builder.memory import make_memory_recall_node

_QUERY = (1.0, 0.0, 0.0)


@dataclass
class _FixedEmbedder:
    """Embeds every text to one fixed query vector."""

    vector: tuple[float, ...] = _QUERY

    async def embed(self, texts: Sequence[str], *, tenant_id: UUID) -> list[tuple[float, ...]]:
        del tenant_id
        return [self.vector for _ in texts]


@dataclass
class _ListStore:
    items: list[MemoryItem]
    limits: list[int] = field(default_factory=list)

    async def retrieve(self, **kwargs: object) -> list[MemoryItem]:
        self.limits.append(int(kwargs["limit"]))  # type: ignore[call-overload]
        return list(self.items)


@dataclass
class _ReverseReranker:
    """Reverses the candidate order — proves rerank feeds MMR, full set."""

    async def rerank(
        self, *, query: str, documents: Sequence[str], top_k: int, tenant_id: UUID
    ) -> list[int]:
        del query, tenant_id
        return list(range(len(documents)))[::-1][:top_k]


def _mem(content: str, embedding: tuple[float, ...], tenant: UUID, user: UUID) -> MemoryItem:
    return MemoryItem(
        id=uuid4(),
        tenant_id=tenant,
        user_id=user,
        kind="fact",
        content=content,
        embedding=embedding,
    )


async def _run(node: object, tenant: UUID, user: UUID) -> list[MemoryItem]:
    out = await node(  # type: ignore[operator]
        {
            "messages": [SystemMessage(content="help"), HumanMessage(content="prefs?")],
            "step_count": 0,
            "max_steps": 5,
        },
        {"configurable": {"tenant_id": str(tenant), "user_id": str(user)}},
    )
    return out.get("recalled_memories", [])


@pytest.mark.asyncio
async def test_near_duplicate_memories_deduplicated_in_top_k() -> None:
    tenant, user = uuid4(), uuid4()
    # Two near-identical candidates + an equally-relevant diverse one
    # (mirrored across the query axis); MMR's redundancy penalty swaps
    # the twin out of the final top-2.
    items = [
        _mem("dup_a", (0.9, 0.43589, 0.0), tenant, user),
        _mem("dup_b", (0.9, 0.43589, 0.0001), tenant, user),
        _mem("diverse", (0.9, -0.43589, 0.0), tenant, user),
    ]
    node = make_memory_recall_node(
        memory_store=_ListStore(items=items),  # type: ignore[arg-type]
        embedder=_FixedEmbedder(),  # type: ignore[arg-type]
        top_k=2,
    )
    recalled = await _run(node, tenant, user)
    assert [m.content for m in recalled] == ["dup_a", "diverse"]


@pytest.mark.asyncio
async def test_rerank_orders_full_set_then_mmr_cuts() -> None:
    tenant, user = uuid4(), uuid4()
    # Zero-vector embeddings make MMR order-preserving — the final order
    # is the reranker's (reversed), cut to top_k by the MMR stage.
    items = [_mem(f"m{i}", (0.0, 0.0, 0.0), tenant, user) for i in range(4)]
    node = make_memory_recall_node(
        memory_store=_ListStore(items=items),  # type: ignore[arg-type]
        embedder=_FixedEmbedder(),  # type: ignore[arg-type]
        top_k=2,
        reranker=_ReverseReranker(),  # type: ignore[arg-type]
    )
    recalled = await _run(node, tenant, user)
    assert [m.content for m in recalled] == ["m3", "m2"]


@pytest.mark.asyncio
async def test_dimension_mismatch_degrades_to_input_order() -> None:
    tenant, user = uuid4(), uuid4()
    # Every candidate embedding mismatches the query dimension — MMR
    # thins to nothing and degrades to the input order, never to empty.
    items = [_mem(f"m{i}", (1.0,), tenant, user) for i in range(3)]
    node = make_memory_recall_node(
        memory_store=_ListStore(items=items),  # type: ignore[arg-type]
        embedder=_FixedEmbedder(),  # type: ignore[arg-type]
        top_k=2,
    )
    recalled = await _run(node, tenant, user)
    assert [m.content for m in recalled] == ["m0", "m1"]
