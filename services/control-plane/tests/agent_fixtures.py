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
from helix_agent.runtime.runs import RunManager
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


def stub_agent_runtime() -> AgentRuntime:
    """Build an :class:`AgentRuntime` whose builder returns a fake-LLM agent."""

    async def _build(spec: object) -> BuiltAgent:
        del spec
        graph = GraphRunner(checkpointer=InMemorySaver()).compile(
            build_react_graph(llm_caller=_fake_llm, tool_registry=ToolRegistry())
        )
        return BuiltAgent(graph=graph, system_prompt="stub system prompt", max_steps=5)

    return AgentRuntime(
        run_manager=RunManager(),
        stream_bridge=InMemoryStreamBridge(),
        agent_builder=_build,
    )
