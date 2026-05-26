"""Shared test fixture — a stub :class:`AgentRuntime`.

The control-plane runs orchestrator graphs in-process. A real
``AgentRuntime`` builds graphs with HTTP provider clients (and needs
real API keys); tests inject this stub instead — its builder ignores
the manifest and returns a :class:`BuiltAgent` over a deterministic
fake ``LLMCaller``, so SSE-endpoint tests exercise the control-plane
wiring without a network call.
"""

from __future__ import annotations

from collections.abc import Sequence

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.checkpoint.memory import InMemorySaver

from control_plane.runtime import AgentRuntime
from helix_agent.runtime.runs import RunEventStore, RunManager, RunStore
from helix_agent.runtime.stream_bridge import InMemoryStreamBridge
from orchestrator import (
    BuiltAgent,
    GraphRunner,
    ToolRegistry,
    ToolSpec,
    build_react_graph,
)


async def _fake_llm(*, messages: Sequence[BaseMessage], tools: Sequence[ToolSpec]) -> AIMessage:
    del messages, tools
    return AIMessage(content="stub agent reply", id="ai-stub")


def stub_agent_runtime(
    *,
    run_store: RunStore | None = None,
    run_event_store: RunEventStore | None = None,
) -> AgentRuntime:
    """Build an :class:`AgentRuntime` whose builder returns a fake-LLM agent.

    ``run_store`` mirrors the production wiring (Mini-ADR J-41) so tests
    that inspect ``/v1/runs`` see the runs the fake LLM actually
    triggered. Pass the same store ``create_app`` uses (`app.state.
    run_store`) for end-to-end coherence.

    ``run_event_store`` (Stream H.3 PR 3 — Mini-ADR H-7) durable-mirrors
    every SSE frame so the ``GET .../events`` endpoint can replay
    terminal runs; pass the same store ``create_app`` uses for tests
    that exercise the replay path.
    """

    async def _build(spec: object) -> BuiltAgent:
        del spec
        graph = GraphRunner(checkpointer=InMemorySaver()).compile(
            build_react_graph(llm_caller=_fake_llm, tool_registry=ToolRegistry())
        )
        return BuiltAgent(graph=graph, system_prompt="stub system prompt", max_steps=5)

    return AgentRuntime(
        run_manager=RunManager(store=run_store),
        stream_bridge=InMemoryStreamBridge(),
        agent_builder=_build,
        run_event_store=run_event_store,
    )
