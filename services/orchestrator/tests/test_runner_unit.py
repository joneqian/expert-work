"""Unit tests for :class:`orchestrator.runner.GraphRunner`.

In-memory checkpointer is enough to prove the wiring contract — Postgres
round-trip is covered separately in ``test_runner_integration.py``.
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from helix_agent.runtime.checkpointer import make_checkpointer
from orchestrator import AgentState, GraphRunner


def _build_echo_graph() -> StateGraph[AgentState, None, AgentState, AgentState]:
    """Trivial graph: one node that appends a fixed AI response."""

    def respond(state: AgentState) -> dict[str, list[BaseMessage]]:
        return {"messages": [AIMessage(content="ok")]}

    graph: StateGraph[AgentState, None, AgentState, AgentState] = StateGraph(AgentState)
    graph.add_node("respond", respond)
    graph.add_edge(START, "respond")
    graph.add_edge("respond", END)
    return graph


@pytest.mark.asyncio
async def test_compile_attaches_checkpointer() -> None:
    """``GraphRunner.compile`` must hand the saver to ``StateGraph.compile``.

    Verified by running the compiled graph once and reading back the
    saved state via ``aget_state`` on the same compiled instance.
    """
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        assert runner.checkpointer is cp

        compiled = runner.compile(_build_echo_graph())
        cfg: RunnableConfig = {"configurable": {"thread_id": "t-unit-1"}}
        await compiled.ainvoke({"messages": [HumanMessage(content="hi")]}, config=cfg)

        snapshot = await compiled.aget_state(cfg)
        contents = [m.content for m in snapshot.values["messages"]]
        assert contents == ["hi", "ok"]


@pytest.mark.asyncio
async def test_checkpoint_visible_across_runner_instances() -> None:
    """Two ``GraphRunner`` instances sharing one saver see each other's state.

    This is the smallest meaningful "restart" simulation possible without
    Postgres: a new ``GraphRunner`` re-compiles the same graph against
    the same saver and must observe the prior run's state.
    """
    async with make_checkpointer("memory") as cp:
        cfg: RunnableConfig = {"configurable": {"thread_id": "t-unit-2"}}

        runner_a = GraphRunner(checkpointer=cp)
        compiled_a = runner_a.compile(_build_echo_graph())
        await compiled_a.ainvoke({"messages": [HumanMessage(content="hi")]}, config=cfg)

        runner_b = GraphRunner(checkpointer=cp)
        compiled_b = runner_b.compile(_build_echo_graph())
        snapshot = await compiled_b.aget_state(cfg)
        contents = [m.content for m in snapshot.values["messages"]]
        assert contents == ["hi", "ok"]


@pytest.mark.asyncio
async def test_thread_isolation() -> None:
    """Different ``thread_id`` values must not see each other's state."""
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        compiled = runner.compile(_build_echo_graph())

        cfg_a: RunnableConfig = {"configurable": {"thread_id": "t-a"}}
        await compiled.ainvoke({"messages": [HumanMessage(content="thread-a")]}, config=cfg_a)

        cfg_b: RunnableConfig = {"configurable": {"thread_id": "t-b"}}
        snapshot_b = await compiled.aget_state(cfg_b)
        assert snapshot_b.values == {}, "fresh thread_id must see empty state"


@pytest.mark.asyncio
async def test_concurrent_two_threads_no_state_bleed() -> None:
    """Two ``ainvoke`` calls on different ``thread_id`` run concurrently must
    not see each other's state.

    Models the race ``test_thread_isolation`` leaves untested: a parent on
    one thread and a sibling on another writing at the same time via
    ``asyncio.gather``.
    """
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        compiled = runner.compile(_build_echo_graph())

        cfg_a: RunnableConfig = {"configurable": {"thread_id": "t-race-a"}}
        cfg_b: RunnableConfig = {"configurable": {"thread_id": "t-race-b"}}

        await asyncio.gather(
            compiled.ainvoke({"messages": [HumanMessage(content="from-a")]}, config=cfg_a),
            compiled.ainvoke({"messages": [HumanMessage(content="from-b")]}, config=cfg_b),
        )

        snap_a = await compiled.aget_state(cfg_a)
        snap_b = await compiled.aget_state(cfg_b)
        assert [m.content for m in snap_a.values["messages"]] == ["from-a", "ok"]
        assert [m.content for m in snap_b.values["messages"]] == ["from-b", "ok"]


@pytest.mark.asyncio
async def test_concurrent_long_sessions_no_pollution() -> None:
    """Three multi-turn sessions running concurrently keep disjoint state.

    Models parallel sibling subagents, each looping its own graph; after all
    finish, every thread holds exactly its own turns and nothing else.
    """
    turns = 5
    async with make_checkpointer("memory") as cp:
        runner = GraphRunner(checkpointer=cp)
        compiled = runner.compile(_build_echo_graph())

        async def session_loop(thread_id: str) -> None:
            cfg: RunnableConfig = {"configurable": {"thread_id": thread_id}}
            for i in range(turns):
                await compiled.ainvoke(
                    {"messages": [HumanMessage(content=f"{thread_id}-turn{i}")]},
                    config=cfg,
                )

        thread_ids = ["t-s1", "t-s2", "t-s3"]
        await asyncio.gather(*(session_loop(tid) for tid in thread_ids))

        for thread_id in thread_ids:
            snap = await compiled.aget_state({"configurable": {"thread_id": thread_id}})
            contents = [m.content for m in snap.values["messages"]]
            # each turn appends the human input plus a fixed "ok" response
            assert len(contents) == turns * 2, f"{thread_id} has wrong message count"
            human = [c for c in contents if c != "ok"]
            assert human == [f"{thread_id}-turn{i}" for i in range(turns)], (
                f"{thread_id} saw another thread's messages"
            )
