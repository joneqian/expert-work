"""Shared test fixture — a stub :class:`AgentRuntime`.

The control-plane runs orchestrator graphs in-process. A real
``AgentRuntime`` builds graphs with HTTP provider clients (and needs
real API keys); tests inject this stub instead — its builder ignores
the manifest and returns a :class:`BuiltAgent` over a deterministic
fake ``LLMCaller``, so SSE-endpoint tests exercise the control-plane
wiring without a network call.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.checkpoint.memory import InMemorySaver

from control_plane.runtime import AgentRuntime
from expert_work.runtime.runs import RunEventStore, RunManager, RunStore
from expert_work.runtime.stream_bridge import InMemoryStreamBridge
from orchestrator import (
    BuiltAgent,
    GraphRunner,
    ToolRegistry,
    ToolSpec,
    build_react_graph,
)


async def _fake_llm(
    *,
    messages: Sequence[BaseMessage],
    tools: Sequence[ToolSpec],
    on_delta: Callable[[Any], Awaitable[None]] | None = None,
) -> AIMessage:
    # ``on_delta`` (子项目 2) — the agent node now passes a token-stream sink on
    # the streaming path; this stub accepts and ignores it (it emits no deltas).
    del messages, tools, on_delta
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

    async def _build(
        spec: object, *, tenant_id: object | None = None, user_id: str | None = None
    ) -> BuiltAgent:
        del spec, tenant_id, user_id
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
