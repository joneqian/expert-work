"""Unit tests for the long-term memory nodes — Stream J.3 PR2b."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from helix_agent.persistence import InMemoryMemoryStore
from helix_agent.protocol import MemoryItem
from helix_agent.runtime.checkpointer import make_checkpointer
from orchestrator import (
    GraphRunner,
    ToolRegistry,
    build_react_graph,
    make_memory_recall_node,
    make_memory_writeback_node,
)
from orchestrator.graph_builder.memory import parse_extracted_memories
from orchestrator.llm import FakeEmbedder
from orchestrator.tools.registry import ToolSpec

_DIM = 32


@dataclass
class _RecordingLLM:
    responses: list[AIMessage]
    calls: list[list[BaseMessage]] = field(default_factory=list)

    async def __call__(
        self, *, messages: Sequence[BaseMessage], tools: Sequence[ToolSpec]
    ) -> AIMessage:
        del tools
        idx = len(self.calls)
        self.calls.append(list(messages))
        if idx >= len(self.responses):
            raise RuntimeError(f"scripted LLM ran out at call {idx}")
        return self.responses[idx]


async def _seed(store: InMemoryMemoryStore, *, tenant: object, user: object, content: str) -> None:
    [vec] = await FakeEmbedder(dim=_DIM).embed([content])
    await store.write(
        [
            MemoryItem(
                id=uuid4(),
                tenant_id=tenant,  # type: ignore[arg-type]
                user_id=user,  # type: ignore[arg-type]
                kind="fact",
                content=content,
                embedding=vec,
            )
        ]
    )


# ---------------------------------------------------------------------------
# parse_extracted_memories
# ---------------------------------------------------------------------------


def test_parse_extracted_memories_clean() -> None:
    out = parse_extracted_memories(
        '{"memories": [{"kind": "fact", "content": "likes Python"}, '
        '{"kind": "episodic", "content": "fixed the bug"}]}'
    )
    assert out == [("fact", "likes Python"), ("episodic", "fixed the bug")]


def test_parse_extracted_memories_drops_bad_kind_and_dedups() -> None:
    out = parse_extracted_memories(
        '{"memories": [{"kind": "fact", "content": "a"}, '
        '{"kind": "bogus", "content": "b"}, '
        '{"kind": "fact", "content": "a"}]}'
    )
    assert out == [("fact", "a")]


@pytest.mark.parametrize(
    "text", ["no json", '{"memories": "not a list"}', "{ bad json }", '{"other": []}']
)
def test_parse_extracted_memories_tolerates_garbage(text: str) -> None:
    assert parse_extracted_memories(text) == []


# ---------------------------------------------------------------------------
# memory_recall node
# ---------------------------------------------------------------------------


def _state(task: str) -> dict[str, object]:
    return {
        "messages": [SystemMessage(content="help"), HumanMessage(content=task)],
        "step_count": 0,
        "max_steps": 5,
    }


@pytest.mark.asyncio
async def test_memory_recall_node_returns_user_memories() -> None:
    store = InMemoryMemoryStore()
    tenant, user = uuid4(), uuid4()
    await _seed(store, tenant=tenant, user=user, content="user prefers metric units")

    node = make_memory_recall_node(memory_store=store, embedder=FakeEmbedder(dim=_DIM), top_k=5)
    out = await node(  # type: ignore[arg-type]
        _state("what's the distance"),
        {"configurable": {"tenant_id": str(tenant), "user_id": str(user)}},
    )
    assert [m.content for m in out["recalled_memories"]] == ["user prefers metric units"]


@pytest.mark.asyncio
async def test_memory_recall_node_noop_without_user_scope() -> None:
    store = InMemoryMemoryStore()
    node = make_memory_recall_node(memory_store=store, embedder=FakeEmbedder(dim=_DIM), top_k=5)
    # No user_id in config → no per-user scope → no recall.
    out = await node(_state("hi"), {"configurable": {"tenant_id": str(uuid4())}})  # type: ignore[arg-type]
    assert out == {}


# ---------------------------------------------------------------------------
# memory_writeback node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_writeback_node_extracts_and_persists() -> None:
    store = InMemoryMemoryStore()
    tenant, user = uuid4(), uuid4()
    llm = _RecordingLLM(
        responses=[AIMessage(content='{"memories": [{"kind": "fact", "content": "likes tea"}]}')]
    )
    node = make_memory_writeback_node(
        memory_store=store, embedder=FakeEmbedder(dim=_DIM), llm_caller=llm
    )
    out = await node(  # type: ignore[arg-type]
        _state("done"),
        {"configurable": {"tenant_id": str(tenant), "user_id": str(user)}},
    )
    assert out == {}
    stored = await store.retrieve(
        tenant_id=tenant, user_id=user, query_embedding=(0.0,) * _DIM, limit=10
    )
    assert [m.content for m in stored] == ["likes tea"]


@pytest.mark.asyncio
async def test_memory_writeback_node_noop_without_user_scope() -> None:
    store = InMemoryMemoryStore()
    llm = _RecordingLLM(responses=[])  # must not be called
    node = make_memory_writeback_node(
        memory_store=store, embedder=FakeEmbedder(dim=_DIM), llm_caller=llm
    )
    out = await node(_state("done"), {"configurable": {"tenant_id": str(uuid4())}})  # type: ignore[arg-type]
    assert out == {}
    assert llm.calls == []


@pytest.mark.asyncio
async def test_memory_writeback_node_swallows_llm_failure() -> None:
    """A write-back failure must never fail the run."""
    store = InMemoryMemoryStore()

    @dataclass
    class _FailingLLM:
        async def __call__(
            self, *, messages: Sequence[BaseMessage], tools: Sequence[ToolSpec]
        ) -> AIMessage:
            raise RuntimeError("llm down")

    node = make_memory_writeback_node(
        memory_store=store, embedder=FakeEmbedder(dim=_DIM), llm_caller=_FailingLLM()
    )
    out = await node(  # type: ignore[arg-type]
        _state("done"),
        {"configurable": {"tenant_id": str(uuid4()), "user_id": str(uuid4())}},
    )
    assert out == {}


# ---------------------------------------------------------------------------
# end-to-end — recall injects, write-back persists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_graph_recalls_and_writes_back() -> None:
    store = InMemoryMemoryStore()
    tenant, user = uuid4(), uuid4()
    await _seed(store, tenant=tenant, user=user, content="user is a botanist")

    embedder = FakeEmbedder(dim=_DIM)
    llm = _RecordingLLM(
        responses=[
            AIMessage(content="answered"),  # agent
            AIMessage(content='{"memories": [{"kind": "fact", "content": "asked about ferns"}]}'),
        ]
    )
    graph = build_react_graph(
        llm_caller=llm,
        tool_registry=ToolRegistry(),
        memory_recall_node=make_memory_recall_node(memory_store=store, embedder=embedder, top_k=5),
        memory_writeback_node=make_memory_writeback_node(
            memory_store=store, embedder=embedder, llm_caller=llm
        ),
    )
    async with make_checkpointer("memory") as cp:
        compiled = GraphRunner(checkpointer=cp).compile(graph)
        await compiled.ainvoke(
            {
                "messages": [SystemMessage(content="help"), HumanMessage(content="ferns?")],
                "step_count": 0,
                "max_steps": 5,
            },
            config={
                "configurable": {
                    "thread_id": "mem-e2e",
                    "tenant_id": str(tenant),
                    "user_id": str(user),
                }
            },
        )

    # The agent's call saw the recalled memory in its system prompt.
    agent_prompt = llm.calls[0]
    system_text = str(agent_prompt[0].content)
    assert "Relevant memories" in system_text
    assert "user is a botanist" in system_text

    # Write-back persisted a new memory for the user.
    stored = await store.retrieve(
        tenant_id=tenant, user_id=user, query_embedding=(0.0,) * _DIM, limit=10
    )
    assert "asked about ferns" in {m.content for m in stored}
