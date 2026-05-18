"""ReAct graph builder — Stream E.6 + E.12.5.

Builds a LangGraph :class:`StateGraph` that implements single-agent
ReAct over :class:`orchestrator.state.AgentState`. The graph has two
nodes wired by a single conditional edge:

::

    START → agent ↔ tools → END
              │
              └─ END (when LLM stops issuing tool_calls or max_steps hit)

The **agent** node delegates the LLM call to an injected
:class:`LLMCaller` (E.11 :class:`LLMRouter` in prod; deterministic fake
in tests) and bumps ``step_count`` by one before returning. Entering with
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

Stream E.12.5 wires the middleware chain into both nodes. Anchor calls
(only when the corresponding chain is passed; ``None`` → no-op):

- ``before_llm_call`` chain → ``agent_node`` invokes before the LLM
  call. ``ctx.payload`` carries ``messages`` / ``tools`` / ``tenant_id``;
  middlewares (E.3 dynamic_context, E.5 pii_redact) may rewrite the
  messages, and E.13 ``cache_lookup`` may set ``llm_cache_hit`` to a
  cached :class:`AIMessage` — when present, ``agent_node`` skips the
  LLM call entirely.
- ``around_llm_call`` chain → handed to :class:`LLMRouter` which
  invokes the chain **per provider** (Mini-ADR E-13), so each
  fallback attempt gets its own E.4 breaker + E.5 langfuse span.
- ``after_llm_call`` chain → ``agent_node`` invokes after the LLM
  returns (or after a cache hit). ``ctx.payload`` carries ``response``
  (mutable) + ``messages`` (running history) + ``prompt_messages``
  (the exact prompt, for E.13 cache-key derivation) + ``tenant_id`` +
  ``cache_hit`` (bool — E.13 ``cache_store`` skips storing a turn that
  was itself served from cache). Middlewares (E.10.5 loop_detection)
  may rewrite the response or append reminder messages.
- ``before_tool_dispatch`` chain → ``tools_node`` invokes per
  ``tool_call``. ``ctx.payload`` carries ``tool_name`` + ``tool_args``;
  middlewares (E.10 sandbox_audit) may raise to block the dispatch.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, cast
from uuid import UUID

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from helix_agent.protocol import Plan
from helix_agent.runtime.cancellation import (
    CANCELLATION_TOKEN_KEY,
    CancellationToken,
)
from helix_agent.runtime.middleware import (
    MiddlewareChain,
    MiddlewareContext,
)
from orchestrator.errors import MaxStepsExceededError
from orchestrator.graph_builder.planner import PlannerNode, render_plan
from orchestrator.llm import LLMCaller
from orchestrator.state import AgentState
from orchestrator.tools.registry import Tool, ToolContext, ToolNotFoundError, ToolRegistry

logger = logging.getLogger(__name__)

#: Truncate raw exception strings before they go to the LLM. Avoids
#: dumping multi-MB tracebacks into messages. Per-tool truncation
#: (E.7/E.8/E.9 + Mini-ADR E-10) still applies to successful results.
_ERROR_SUMMARY_MAX_CHARS = 500


async def _noop(_ctx: MiddlewareContext) -> None:
    """Default terminal for non-around anchors — middlewares run their
    pre-/post-``call_next`` logic, but there's no inner work to wrap."""


def build_react_graph(
    *,
    llm_caller: LLMCaller,
    tool_registry: ToolRegistry,
    planner_node: PlannerNode | None = None,
    before_llm_chain: MiddlewareChain | None = None,
    after_llm_chain: MiddlewareChain | None = None,
    before_tool_dispatch_chain: MiddlewareChain | None = None,
) -> StateGraph[AgentState, None, AgentState, AgentState]:
    """Assemble the ReAct ``StateGraph`` and return it uncompiled.

    Caller (typically :class:`orchestrator.runner.GraphRunner`)
    compiles it with the shared checkpointer.

    When ``planner_node`` is supplied (Stream J.1 — manifest
    ``workflow.type == "plan_execute"``) the graph is fronted by a
    ``planner`` node: ``START → planner → agent``. The planner writes
    ``AgentState.plan`` and ``agent_node`` renders it into its system
    context every step. ``None`` → plain ``START → agent`` ReAct.

    All chain arguments are optional — ``None`` means "no middleware at
    this anchor", and ``agent_node`` / ``tools_node`` short-circuit the
    chain invocation entirely. This preserves the M0 unit-test path
    that doesn't boot a chain.

    The ``around_llm_call`` chain is **not** a parameter here — it
    belongs to :class:`LLMRouter`, which wraps each provider call
    individually (Mini-ADR E-13). Callers configure it on the router
    at construction time.
    """

    async def agent_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        token = _cancellation_token(config)
        token.raise_if_cancelled()

        step_count = state.get("step_count", 0)
        max_steps = state.get("max_steps", 0)
        if step_count >= max_steps:
            raise MaxStepsExceededError(step_count=step_count, max_steps=max_steps)

        tools = list(tool_registry.specs())
        messages = list(state["messages"])
        # Stream J.1 — render the plan into the system context so every
        # ReAct step executes against it. No-op for plain ReAct graphs.
        plan = state.get("plan")
        if plan is not None:
            messages = _inject_plan(messages, plan)
        configurable = config.get("configurable") or {}
        tenant_id = _parse_uuid(configurable.get("tenant_id"))

        cache_hit_response: AIMessage | None = None
        if before_llm_chain is not None:
            ctx = MiddlewareContext(
                payload={"messages": messages, "tools": tools, "tenant_id": tenant_id}
            )
            await before_llm_chain.invoke(ctx, _noop)
            messages = list(ctx.payload.get("messages", messages))
            tools = list(ctx.payload.get("tools", tools))
            hit = ctx.payload.get("llm_cache_hit")
            if isinstance(hit, AIMessage):
                cache_hit_response = hit

        # ``messages`` is now the exact prompt — the E.13 cache key input.
        if cache_hit_response is not None:
            response: AIMessage = cache_hit_response
        else:
            # Wrap the LLM call so a cancel mid-call interrupts the
            # in-flight await rather than waiting it out (E.15).
            response = await token.run_cancellable(llm_caller(messages=messages, tools=tools))

        if after_llm_chain is not None:
            after_messages: list[BaseMessage] = [*messages, response]
            ctx = MiddlewareContext(
                payload={
                    "messages": after_messages,
                    "response": response,
                    "tenant_id": tenant_id,
                    "prompt_messages": messages,
                    "cache_hit": cache_hit_response is not None,
                }
            )
            await after_llm_chain.invoke(ctx, _noop)
            new_messages = _extract_post_llm_messages(ctx, original=after_messages)
            return {"messages": new_messages, "step_count": step_count + 1}

        return {"messages": [response], "step_count": step_count + 1}

    async def tools_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        token = _cancellation_token(config)
        token.raise_if_cancelled()

        last = state["messages"][-1]
        tool_calls = _extract_tool_calls(last)
        if not tool_calls:
            return {}

        ctx_obj = _build_tool_context(config)
        new_messages: list[BaseMessage] = []
        for tc in tool_calls:
            token.raise_if_cancelled()
            # ``run_cancellable`` interrupts a slow tool mid-dispatch. A
            # cancel surfaces as ``asyncio.CancelledError`` inside
            # ``_dispatch_tool`` — which is *not* an ``Exception``, so
            # the tool's own ``except Exception`` won't swallow it into
            # a ToolMessage; ``run_cancellable`` re-raises it as
            # ``RunCancelledError``.
            new_messages.append(
                await token.run_cancellable(
                    _dispatch_tool(
                        tc,
                        tool_registry,
                        ctx_obj,
                        before_tool_dispatch_chain=before_tool_dispatch_chain,
                    )
                )
            )
        return {"messages": new_messages}

    graph: StateGraph[AgentState, None, AgentState, AgentState] = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    if planner_node is not None:
        # ``PlannerNode`` is structurally a ``(state, config)`` node but
        # mypy can't match the bare Callable alias to LangGraph's
        # internal ``_NodeWithConfig`` overloads — same gap runs.py
        # documents for the StreamableGraph protocol.
        graph.add_node("planner", planner_node)  # type: ignore[arg-type]
        graph.add_edge(START, "planner")
        graph.add_edge("planner", "agent")
    else:
        graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", _should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph


def _inject_plan(messages: list[BaseMessage], plan: Plan) -> list[BaseMessage]:
    """Merge the rendered plan into the prompt's leading system message.

    Returns a new list — the checkpointed ``state['messages']`` is left
    untouched; the plan rides only in this per-call prompt (``plan``
    itself is the checkpointed source of truth).
    """
    rendered = render_plan(plan)
    if messages and isinstance(messages[0], SystemMessage):
        head = messages[0]
        head_text = head.content if isinstance(head.content, str) else str(head.content)
        merged = SystemMessage(content=f"{head_text}\n\n{rendered}")
        return [merged, *messages[1:]]
    return [SystemMessage(content=rendered), *messages]


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


def _extract_post_llm_messages(
    ctx: MiddlewareContext,
    *,
    original: list[BaseMessage],
) -> list[BaseMessage]:
    """Decode what ``after_llm_call`` middlewares left in ``ctx``.

    Convention:
    - ``ctx.payload["messages"]`` is the updated message list; we
      return the suffix beyond the original prefix so LangGraph's
      ``add_messages`` reducer appends exactly the new tail.
    - If the chain returned a strictly-shorter list (e.g., E.10.5
      loop_detection rewrites the trailing AIMessage and appends a
      reminder), we return that list as-is — same-id messages cause
      ``add_messages`` to replace the prior copy rather than duplicate.
    """
    updated = ctx.payload.get("messages")
    if not isinstance(updated, list):
        response = ctx.payload.get("response")
        return [response] if isinstance(response, AIMessage) else []

    original_len = len(original) - 1  # exclude the freshly-appended response
    if len(updated) >= original_len:
        prefix_unchanged = updated[:original_len] == original[:original_len]
        if prefix_unchanged:
            return list(updated[original_len:])
    return list(updated)


async def _dispatch_tool(
    tool_call: dict[str, Any],
    registry: ToolRegistry,
    ctx: ToolContext,
    *,
    before_tool_dispatch_chain: MiddlewareChain | None,
) -> ToolMessage:
    name = str(tool_call.get("name", ""))
    call_id = str(tool_call.get("id", ""))
    args = tool_call.get("args") or {}

    try:
        if before_tool_dispatch_chain is not None:
            mw_ctx = MiddlewareContext(payload={"tool_name": name, "tool_args": dict(args)})
            await before_tool_dispatch_chain.invoke(mw_ctx, _noop)
            # Middlewares may rewrite tool_args (e.g., redact PII before
            # dispatch); tool_name is treated as immutable.
            args = mw_ctx.payload.get("tool_args", args) or {}

        tool = registry.get_required(name)
        return await _invoke_tool(tool, args, call_id, ctx)
    except ToolNotFoundError as exc:
        logger.warning("tools.unknown_tool name=%s call_id=%s", name, call_id)
        return ToolMessage(
            content=_format_error(exc),
            tool_call_id=call_id,
            status="error",
        )
    except Exception as exc:
        # E.10 sandbox_audit and any other pre-dispatch middleware raise
        # to block — wrap so the LLM sees a normal error result rather
        # than the run crashing (Mini-ADR E-12).
        logger.warning(
            "tools.before_dispatch_blocked name=%s call_id=%s err=%s",
            name,
            call_id,
            type(exc).__name__,
        )
        return ToolMessage(
            content=_format_error(exc),
            tool_call_id=call_id,
            status="error",
        )


def _cancellation_token(config: RunnableConfig) -> CancellationToken:
    """Lift the run's :class:`CancellationToken` out of ``config``.

    The token travels via ``config["configurable"]`` (not ``AgentState``
    — a live :class:`asyncio.Event` is not checkpoint-serialisable).
    When absent — dev / unit-test path that never cancels — a fresh,
    never-cancelled token is returned so node code is uniform.
    """
    configurable = config.get("configurable") or {}
    token = configurable.get(CANCELLATION_TOKEN_KEY)
    if isinstance(token, CancellationToken):
        return token
    return CancellationToken()


def _build_tool_context(config: RunnableConfig) -> ToolContext:
    """Lift tenant binding out of ``config["configurable"]`` into a
    :class:`ToolContext`. Missing values fall through as ``None`` —
    M0 dev / unit tests rarely supply tenant_id, and per-tenant tools
    (E.8 HTTP, E.9 MCP) handle the ``None`` case explicitly (deny-all)."""
    configurable = config.get("configurable") or {}
    tenant_id = _parse_uuid(configurable.get("tenant_id"))
    run_id = _parse_uuid(configurable.get("run_id"))
    return ToolContext(tenant_id=tenant_id, run_id=run_id)


def _parse_uuid(raw: object) -> UUID | None:
    if isinstance(raw, UUID):
        return raw
    if isinstance(raw, str):
        try:
            return UUID(raw)
        except ValueError:
            return None
    return None


async def _invoke_tool(
    tool: Tool,
    args: dict[str, Any],
    call_id: str,
    ctx: ToolContext,
) -> ToolMessage:
    try:
        result = await tool.call(args, ctx=ctx)
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
