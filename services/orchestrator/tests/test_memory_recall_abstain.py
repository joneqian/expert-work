"""Stream P5a (Task 8) — abstention threshold gate on memory recall.

When ``abstain_threshold`` is set above ``0.0`` and wired into
``make_memory_recall_node``, ``memory_recall_node`` computes the cosine
similarity between the query embedding and every recalled candidate
(``_reconcile_cosine`` — already a similarity in [-1, 1], not a distance)
right after ``retrieve()`` returns, before the rerank / MMR / verify stages.
If the *best* candidate's similarity falls below the threshold, the node
bumps ``record_memory_abstain`` and returns ``{}`` (no ``recalled_memories``
key) instead of forcing a weak match on the agent. The default threshold is
``0.0``, which never abstains (``abstain_threshold > 0.0`` gates the whole
check) — existing callers that don't pass the parameter are unaffected.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from prometheus_client import REGISTRY

from expert_work.protocol import MemoryItem
from orchestrator.graph_builder.memory import _reconcile_cosine, make_memory_recall_node

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
    """Returns a fixed candidate list, ignoring the query embedding."""

    items: list[MemoryItem]

    async def retrieve(self, **kwargs: object) -> list[MemoryItem]:
        del kwargs
        return list(self.items)

    async def bump_access(self, *, tenant_id: UUID, user_id: UUID, ids: Sequence[UUID]) -> None:
        return None


def _mem(content: str, embedding: tuple[float, ...], tenant: UUID, user: UUID) -> MemoryItem:
    return MemoryItem(
        id=uuid4(),
        tenant_id=tenant,
        user_id=user,
        kind="fact",
        content=content,
        embedding=embedding,
    )


def _state(task: str = "prefs?") -> dict[str, object]:
    return {
        "messages": [SystemMessage(content="help"), HumanMessage(content=task)],
        "step_count": 0,
        "max_steps": 5,
    }


def _abstain_count() -> float:
    """Read the live counter sample. Tests are not strict on absolute
    values (other tests in the suite bump it) — they assert deltas."""
    value = REGISTRY.get_sample_value("expert_work_memory_abstain_total")
    return float(value) if value is not None else 0.0


@pytest.mark.asyncio
async def test_abstain_returns_empty_when_top_similarity_below_threshold() -> None:
    tenant, user = uuid4(), uuid4()
    # Orthogonal to the query → cosine = 0.0, well below the threshold.
    items = [_mem("far", (0.0, 1.0, 0.0), tenant, user)]
    node = make_memory_recall_node(
        memory_store=_ListStore(items=items),  # type: ignore[arg-type]
        embedder=_FixedEmbedder(),  # type: ignore[arg-type]
        top_k=5,
        abstain_threshold=0.5,
    )
    before = _abstain_count()
    out = await node(  # type: ignore[arg-type]
        _state(),
        {"configurable": {"tenant_id": str(tenant), "user_id": str(user)}},
    )
    assert out == {}
    assert _abstain_count() - before == 1.0


@pytest.mark.asyncio
async def test_no_abstain_when_top_similarity_meets_threshold() -> None:
    tenant, user = uuid4(), uuid4()
    # Identical to the query → cosine = 1.0, comfortably above the threshold.
    items = [_mem("close", (1.0, 0.0, 0.0), tenant, user)]
    node = make_memory_recall_node(
        memory_store=_ListStore(items=items),  # type: ignore[arg-type]
        embedder=_FixedEmbedder(),  # type: ignore[arg-type]
        top_k=5,
        abstain_threshold=0.5,
    )
    before = _abstain_count()
    out = await node(  # type: ignore[arg-type]
        _state(),
        {"configurable": {"tenant_id": str(tenant), "user_id": str(user)}},
    )
    assert [m.content for m in out["recalled_memories"]] == ["close"]
    assert _abstain_count() - before == 0.0


@pytest.mark.asyncio
async def test_abstain_uses_max_similarity_across_all_candidates() -> None:
    # The weak candidate sorts first — a gate that only inspected memories[0]
    # would wrongly abstain even though the *best* candidate clears the bar.
    tenant, user = uuid4(), uuid4()
    items = [
        _mem("far", (0.0, 1.0, 0.0), tenant, user),  # cosine 0.0
        _mem("close", (1.0, 0.0, 0.0), tenant, user),  # cosine 1.0
    ]
    node = make_memory_recall_node(
        memory_store=_ListStore(items=items),  # type: ignore[arg-type]
        embedder=_FixedEmbedder(),  # type: ignore[arg-type]
        top_k=5,
        abstain_threshold=0.5,
    )
    out = await node(  # type: ignore[arg-type]
        _state(),
        {"configurable": {"tenant_id": str(tenant), "user_id": str(user)}},
    )
    assert {m.content for m in out["recalled_memories"]} == {"far", "close"}


@pytest.mark.asyncio
async def test_no_abstain_at_exact_threshold_boundary() -> None:
    # top_sim == threshold must NOT abstain (strict "<", not "<="): compute
    # the exact cosine the node will compute and use it as the threshold.
    tenant, user = uuid4(), uuid4()
    embedding = (1.0, 1.0, 0.0)
    threshold = _reconcile_cosine(_QUERY, embedding)
    items = [_mem("borderline", embedding, tenant, user)]
    node = make_memory_recall_node(
        memory_store=_ListStore(items=items),  # type: ignore[arg-type]
        embedder=_FixedEmbedder(),  # type: ignore[arg-type]
        top_k=5,
        abstain_threshold=threshold,
    )
    before = _abstain_count()
    out = await node(  # type: ignore[arg-type]
        _state(),
        {"configurable": {"tenant_id": str(tenant), "user_id": str(user)}},
    )
    assert [m.content for m in out["recalled_memories"]] == ["borderline"]
    assert _abstain_count() - before == 0.0


@pytest.mark.asyncio
async def test_default_threshold_never_abstains() -> None:
    tenant, user = uuid4(), uuid4()
    # Opposite direction from the query → cosine = -1.0, the worst possible
    # similarity — still must not abstain when abstain_threshold is unset.
    items = [_mem("opposite", (-1.0, 0.0, 0.0), tenant, user)]
    node = make_memory_recall_node(
        memory_store=_ListStore(items=items),  # type: ignore[arg-type]
        embedder=_FixedEmbedder(),  # type: ignore[arg-type]
        top_k=5,
    )
    before = _abstain_count()
    out = await node(  # type: ignore[arg-type]
        _state(),
        {"configurable": {"tenant_id": str(tenant), "user_id": str(user)}},
    )
    assert [m.content for m in out["recalled_memories"]] == ["opposite"]
    assert _abstain_count() - before == 0.0


def _retrieval_count(result: str, mode: str = "hybrid") -> float:
    value = REGISTRY.get_sample_value(
        "expert_work_uplift_memory_retrieval_total", {"mode": mode, "result": result}
    )
    return float(value) if value is not None else 0.0


@pytest.mark.asyncio
async def test_abstain_also_records_retrieval_miss() -> None:
    """P5a assembly — an abstained recall counts as a retrieval *miss* for
    observability. The abstain gate returns before the node's normal
    ``record_memory_retrieval`` call, so without an explicit bump the abstain
    branch would silently skip the retrieval metric entirely."""
    tenant, user = uuid4(), uuid4()
    items = [_mem("far", (0.0, 1.0, 0.0), tenant, user)]  # cosine 0.0 → abstains
    node = make_memory_recall_node(
        memory_store=_ListStore(items=items),  # type: ignore[arg-type]
        embedder=_FixedEmbedder(),  # type: ignore[arg-type]
        top_k=5,
        abstain_threshold=0.5,
    )
    # No tenant_config_store wired → recall mode defaults to hybrid.
    miss_before = _retrieval_count("miss")
    hit_before = _retrieval_count("hit")
    out = await node(  # type: ignore[arg-type]
        _state(),
        {"configurable": {"tenant_id": str(tenant), "user_id": str(user)}},
    )
    assert out == {}
    assert _retrieval_count("miss") - miss_before == 1.0
    assert _retrieval_count("hit") - hit_before == 0.0
