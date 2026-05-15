"""ReAct graph builder — Stream E.6.

Builds a LangGraph :class:`StateGraph` that implements single-agent
ReAct over :class:`orchestrator.state.AgentState`. The graph has two
nodes wired by a single conditional edge:

::

    START → agent ↔ tools → END
              │
              └─ END (when LLM stops issuing tool_calls or max_steps hit)

The **agent** node delegates the LLM call to an injected
:class:`LLMCaller` (E.11 LLMRouter in prod; deterministic fake in
tests) and bumps ``step_count`` by one before returning. Entering with
``step_count >= max_steps`` raises :class:`MaxStepsExceededError` so the
runner can finalise the run with ``RUN_FAILED`` audit + user-facing
"reached max_steps" message.

The **tools** node walks the most-recent ``AIMessage.tool_calls``,
dispatches each through :class:`ToolRegistry`, and appends one
``ToolMessage`` per call to the messages list. Any uncaught tool
exception (including ``ToolNotFoundError`` for unknown names) is
wrapped into ``ToolMessage(content="[tool error] ...")`` rather than
re-raised, per Mini-ADR E-12 — the LLM sees the error as a tool result
and reasons about retry / different args / final answer.

This PR deliberately does **not** wire the E.3 / E.4 / E.5 middleware
chains into the agent node. That wiring happens when E.11 LLMRouter
lands (it owns the actual LLM call, so anchoring middlewares there
keeps responsibilities clean). E.6 tests use a mock ``LLMCaller`` and
exercise the loop / dispatch / error-wrap mechanics directly.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, cast

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from orchestrator.errors import MaxStepsExceededError
from orchestrator.llm import LLMCaller
from orchestrator.state import AgentState
from orchestrator.tools.registry import Tool, ToolNotFoundError, ToolRegistry

logger = logging.getLogger(__name__)

#: Truncate raw exception strings before they go to the LLM. Avoids
#: dumping multi-MB tracebacks into messages. Per-tool truncation
#: (E.7/E.8/E.9 + Mini-ADR E-10) still applies to successful results.
_ERROR_SUMMARY_MAX_CHARS = 500


def build_react_graph(
    *,
    llm_caller: LLMCaller,
    tool_registry: ToolRegistry,
) -> StateGraph[AgentState, None, AgentState, AgentState]:
    """Assemble the ReAct ``StateGraph`` and return it uncompiled.

    Caller (typically :class:`orchestrator.runner.GraphRunner`)
    compiles it with the shared checkpointer.
    """

    async def agent_node(state: AgentState) -> dict[str, Any]:
        step_count = state.get("step_count", 0)
        max_steps = state.get("max_steps", 0)
        if step_count >= max_steps:
            raise MaxStepsExceededError(step_count=step_count, max_steps=max_steps)

        response = await llm_caller(
            messages=state["messages"],
            tools=tool_registry.specs(),
        )
        return {"messages": [response], "step_count": step_count + 1}

    async def tools_node(state: AgentState) -> dict[str, Any]:
        last = state["messages"][-1]
        tool_calls = _extract_tool_calls(last)
        if not tool_calls:
            return {}

        new_messages: list[BaseMessage] = []
        for tc in tool_calls:
            new_messages.append(await _dispatch_tool(tc, tool_registry))
        return {"messages": new_messages}

    graph: StateGraph[AgentState, None, AgentState, AgentState] = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", _should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph


def _should_continue(state: AgentState) -> Literal["tools", "__end__"]:
    last = state["messages"][-1]
    if _extract_tool_calls(last):
        return "tools"
    return "__end__"


def _extract_tool_calls(message: BaseMessage) -> list[dict[str, Any]]:
    """Return ``AIMessage.tool_calls`` if present, else empty list.

    LangChain represents tool_calls as a list of ``{name, args, id}``
    dicts; non-AI messages never carry them.
    """
    if not isinstance(message, AIMessage):
        return []
    raw = getattr(message, "tool_calls", None)
    if not raw:
        return []
    return cast(list[dict[str, Any]], raw)


async def _dispatch_tool(tool_call: dict[str, Any], registry: ToolRegistry) -> ToolMessage:
    name = str(tool_call.get("name", ""))
    call_id = str(tool_call.get("id", ""))
    args = tool_call.get("args") or {}

    try:
        tool = registry.get_required(name)
        return await _invoke_tool(tool, args, call_id)
    except ToolNotFoundError as exc:
        logger.warning("tools.unknown_tool name=%s call_id=%s", name, call_id)
        return ToolMessage(
            content=_format_error(exc),
            tool_call_id=call_id,
            status="error",
        )


async def _invoke_tool(
    tool: Tool,
    args: dict[str, Any],
    call_id: str,
) -> ToolMessage:
    try:
        result = await tool.call(args)
    except Exception as exc:
        logger.warning(
            "tools.dispatch_failed name=%s call_id=%s err=%s",
            tool.spec.name,
            call_id,
            type(exc).__name__,
        )
        return ToolMessage(
            content=_format_error(exc),
            tool_call_id=call_id,
            status="error",
        )
    return ToolMessage(content=result.content, tool_call_id=call_id)


def _format_error(exc: BaseException) -> str:
    summary = str(exc)
    if len(summary) > _ERROR_SUMMARY_MAX_CHARS:
        summary = summary[:_ERROR_SUMMARY_MAX_CHARS] + "...[truncated]"
    return f"[tool error] {type(exc).__name__}: {summary}"
