"""Postgres-backed integration test for :class:`orchestrator.runner.GraphRunner`.

Two distinct ``make_checkpointer("postgres", dsn)`` contexts simulate a
service restart: the second context creates a brand-new
``AsyncPostgresSaver`` instance pointing at the same database, and must
read the state written by the first context.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from testcontainers.postgres import PostgresContainer

from expert_work.runtime.checkpointer import make_checkpointer
from orchestrator import AgentState, GraphRunner

pytestmark = pytest.mark.integration


def _sync_dsn(container: PostgresContainer) -> str:
    """``AsyncPostgresSaver`` takes a sync-style DSN; strip the testcontainers
    ``+psycopg2`` driver suffix to match what LangGraph expects."""
    return str(container.get_connection_url()).replace("+psycopg2", "")


def _build_echo_graph() -> StateGraph[AgentState, None, AgentState, AgentState]:
    def respond(state: AgentState) -> dict[str, list[BaseMessage]]:
        return {"messages": [AIMessage(content="ok")]}

    graph: StateGraph[AgentState, None, AgentState, AgentState] = StateGraph(AgentState)
    graph.add_node("respond", respond)
    graph.add_edge(START, "respond")
    graph.add_edge("respond", END)
    return graph


@pytest.mark.asyncio
async def test_postgres_checkpoint_persists_across_restart(
    postgres_container: PostgresContainer,
) -> None:
    """State written under one ``make_checkpointer`` context must survive
    a fresh context against the same DSN — proving Postgres durability."""
    dsn = _sync_dsn(postgres_container)
    cfg: RunnableConfig = {"configurable": {"thread_id": "t-e1-integ-restart"}}

    async with make_checkpointer("postgres", dsn) as cp_first:
        runner = GraphRunner(checkpointer=cp_first)
        compiled = runner.compile(_build_echo_graph())
        await compiled.ainvoke({"messages": [HumanMessage(content="hi")]}, config=cfg)

    # Fresh saver context — equivalent to a process restart.
    async with make_checkpointer("postgres", dsn) as cp_second:
        runner_after_restart = GraphRunner(checkpointer=cp_second)
        compiled_after_restart = runner_after_restart.compile(_build_echo_graph())
        snapshot = await compiled_after_restart.aget_state(cfg)
        contents = [m.content for m in snapshot.values["messages"]]
        assert contents == ["hi", "ok"]
