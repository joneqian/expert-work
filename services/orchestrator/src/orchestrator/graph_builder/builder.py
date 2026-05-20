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

import asyncio
import itertools
import logging
from collections.abc import Mapping
from typing import Any, Literal, cast
from uuid import UUID

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from helix_agent.common.observability import helix_counter
from helix_agent.protocol import MemoryItem, Plan
from helix_agent.runtime.middleware import (
    MiddlewareChain,
    MiddlewareContext,
)
from orchestrator.errors import MaxStepsExceededError
from orchestrator.graph_builder._config import cancellation_token
from orchestrator.graph_builder.memory import MemoryNode
from orchestrator.graph_builder.planner import PlannerNode, render_plan
from orchestrator.graph_builder.reflect import ReflectNode
from orchestrator.llm import LLMCaller
from orchestrator.state import AgentState
from orchestrator.tools.mutation_classifier import MutationOutcome
from orchestrator.tools.mutation_classifier import classify as classify_mutation
from orchestrator.tools.registry import (
    TOOL_ALLOWED_STATE_KEYS,
    Tool,
    ToolContext,
    ToolNotFoundError,
    ToolRegistry,
)
from orchestrator.tools.scheduling import MAX_TOOL_WORKERS, plan_stages

logger = logging.getLogger(__name__)

# Stream L.L6 — counters for the adaptive tool scheduler. ``stages_total``
# counts every stage execution; ``dispatched_total`` counts the underlying
# tool calls. The ratio dispatched / stages gives the average per-stage
# concurrency (1.0 == fully sequential, MAX_TOOL_WORKERS == max parallel).
# Two counters instead of a histogram because validate_metric_name reserves
# histograms for duration-shaped ``_seconds`` metrics.
_tools_stages_total = helix_counter(
    "helix_tools_stages_total",
    "Tool-call stages executed (Stream L.L6).",
)
_tools_dispatched_total = helix_counter(
    "helix_tools_dispatched_total",
    (
        "Individual tool calls dispatched within L6 stages — divide by "
        "stages to get average concurrency."
    ),
)

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
    reflect_node: ReflectNode | None = None,
    memory_recall_node: MemoryNode | None = None,
    memory_writeback_node: MemoryNode | None = None,
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

    When ``reflect_node`` is supplied (Stream J.2 — manifest
    ``reflection:`` block) the agent's no-tool-calls exit routes through
    a ``reflect`` node that self-critiques and may loop back to the
    agent instead of ending. ``None`` → the agent ends directly.

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
        token = cancellation_token(config)
        token.raise_if_cancelled()

        # Stream L.L5 — consume any pending refund the previous tools
        # node wrote (Mini-ADR L-5). Internal-chain tools like
        # ``update_plan`` (K.K8) ask the loop to refund their
        # iterations so housekeeping doesn't burn user-visible budget.
        # Clamp at 0: refund can never produce a negative step count
        # (defensive invariant — a tool can't push the agent into a
        # nonsense negative budget).
        raw_step_count = state.get("step_count", 0)
        refund_pending = state.get("step_count_refund_pending", 0)
        step_count = max(0, raw_step_count - refund_pending)
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
        # Stream J.3 — render recalled long-term memories into context.
        memories = state.get("recalled_memories")
        if memories:
            messages = _inject_memories(messages, memories)
        # Stream L.L4 — inject a ``<mutation-advisory>`` HumanMessage
        # listing file mutations that did NOT land in the previous
        # tools batch. Mini-ADR L-4: the advisory is part of the
        # conversation history (persists across turns) and lives in a
        # HumanMessage, NOT the system block, so the L1 prompt-cache
        # prefix invariant stays intact. Append once per failure batch
        # — the channel is reset to ``[]`` in this node's return dict
        # so a follow-on agent step does not double-inject.
        failed_mutations = list(state.get("failed_mutations", []))
        advisory_message: HumanMessage | None = None
        if failed_mutations:
            advisory_message = _build_mutation_advisory(failed_mutations)
            messages = [*messages, advisory_message]
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
            # Stream L.L4 — persist the advisory into history so the
            # next agent step sees it even after this dict's reducer
            # appends. The middleware path's ``new_messages`` is the
            # full post-LLM delta; prepend the advisory in case the
            # middleware filtered the prompt body.
            persisted_messages: list[BaseMessage] = list(new_messages)
            if advisory_message is not None and advisory_message not in persisted_messages:
                persisted_messages = [advisory_message, *persisted_messages]
            return {
                "messages": persisted_messages,
                "step_count": step_count + 1,
                "step_count_refund_pending": 0,
                "failed_mutations": [],
            }

        # Stream L.L4 — persist the advisory in conversation history
        # alongside the LLM response so the next agent step sees it.
        emit_messages: list[BaseMessage] = (
            [advisory_message, response] if advisory_message is not None else [response]
        )
        return {
            "messages": emit_messages,
            "step_count": step_count + 1,
            "step_count_refund_pending": 0,
            "failed_mutations": [],
        }

    async def tools_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        token = cancellation_token(config)
        token.raise_if_cancelled()

        last = state["messages"][-1]
        tool_calls = _extract_tool_calls(last)
        if not tool_calls:
            return {}

        ctx_obj = _build_tool_context(config, plan=state.get("plan"))
        # Stream L.L6 — group tool_calls into stages of mutually-non-
        # conflicting calls. Within a stage we ``asyncio.gather`` (capped
        # at MAX_TOOL_WORKERS); stages execute sequentially so any
        # state-mutating call (``update_plan``, ``save_artifact`` on a
        # contested path) still observes the LLM's intended ordering.
        specs_by_name = {spec.name: spec for spec in tool_registry.specs()}
        stages = plan_stages(tool_calls, specs_by_name)
        results: dict[int, tuple[ToolMessage, Mapping[str, Any], int]] = {}
        # Stream K.K8 — collect per-tool state writes for promotion to
        # the AgentState update dict. Order follows the LLM's original
        # tool_call sequence: a later call's update wins. L6 preserves
        # that because we apply updates in original-index order after
        # stages complete.
        accumulated_state: dict[str, Any] = {}
        # Stream L.L5 — accumulate iteration refunds across the batch.
        # Refunds are commutative, so stage ordering doesn't affect the
        # total. Seed from any pending refund the previous node left
        # unconsumed (defence-in-depth — agent_node also resets).
        refund_total = state.get("step_count_refund_pending", 0)

        async def _run_call(
            tc: dict[str, Any],
        ) -> tuple[ToolMessage, Mapping[str, Any], int]:
            # Per-call cancel check + ``run_cancellable`` mirror the M0
            # sequential path so cancellation semantics stay identical:
            # a cancel mid-batch interrupts every in-flight tool via
            # the shared token.
            token.raise_if_cancelled()
            return await token.run_cancellable(
                _dispatch_tool(
                    tc,
                    tool_registry,
                    ctx_obj,
                    before_tool_dispatch_chain=before_tool_dispatch_chain,
                )
            )

        semaphore = asyncio.Semaphore(MAX_TOOL_WORKERS)

        async def _bounded(tc: dict[str, Any]) -> tuple[ToolMessage, Mapping[str, Any], int]:
            async with semaphore:
                return await _run_call(tc)

        for stage in stages:
            _tools_stages_total.inc()
            _tools_dispatched_total.inc(len(stage))
            # ``return_exceptions=False`` — any exception from a tool
            # already comes back wrapped as a ToolMessage by
            # ``_dispatch_tool``; reaching gather with a raw exception
            # would be ``RunCancelledError`` (cancellation) or a
            # programmer error, both of which should propagate.
            stage_results = await asyncio.gather(
                *(_bounded(tool_calls[call.index]) for call in stage)
            )
            for call, result in zip(stage, stage_results, strict=True):
                results[call.index] = result

        # Re-assemble in original tool_call order. L5 / K8 invariants
        # require a stable iteration order downstream.
        new_messages: list[BaseMessage] = []
        # Stream L.L4 — collect mutations that did NOT land so the next
        # agent step can inject the advisory footer. The check runs in
        # original order so the advisory lists failures the LLM would
        # see in the same sequence the ToolMessages appear.
        failed_mutations: list[MutationOutcome] = []
        for idx in range(len(tool_calls)):
            tool_message, tool_state, refund_inc = results[idx]
            new_messages.append(tool_message)
            for key, value in tool_state.items():
                if key in TOOL_ALLOWED_STATE_KEYS:
                    accumulated_state[key] = value
            refund_total += refund_inc
            outcome = classify_mutation(
                str(tool_calls[idx].get("name", "")),
                tool_calls[idx].get("args") or {},
                tool_message,
            )
            if outcome is not None and not outcome.landed:
                failed_mutations.append(outcome)

        result_dict: dict[str, Any] = {
            "messages": new_messages,
            "step_count_refund_pending": refund_total,
            **accumulated_state,
        }
        # Only write the channel when there are failures — the absent
        # case keeps the agent_node's ``state.get("failed_mutations", [])``
        # default fast-path active.
        if failed_mutations:
            result_dict["failed_mutations"] = failed_mutations
        return result_dict

    graph: StateGraph[AgentState, None, AgentState, AgentState] = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)

    # Entry chain: START → [memory_recall] → [planner] → agent — each
    # node optional, in this fixed order. ``# type: ignore[arg-type]``:
    # the bare Callable node aliases don't match LangGraph's internal
    # ``_NodeWithConfig`` overloads (same gap runs.py documents).
    entry: list[str] = [START]
    if memory_recall_node is not None:
        graph.add_node("memory_recall", memory_recall_node)  # type: ignore[arg-type]
        entry.append("memory_recall")
    if planner_node is not None:
        graph.add_node("planner", planner_node)  # type: ignore[arg-type]
        entry.append("planner")
    for src, dst in itertools.pairwise(entry):
        graph.add_edge(src, dst)
    graph.add_edge(entry[-1], "agent")

    # Exit: the run's end routes through ``memory_writeback`` when present.
    end_target: str = END
    if memory_writeback_node is not None:
        graph.add_node("memory_writeback", memory_writeback_node)  # type: ignore[arg-type]
        graph.add_edge("memory_writeback", END)
        end_target = "memory_writeback"

    if reflect_node is not None:
        # When the agent stops issuing tool_calls, route to ``reflect``
        # instead of ending — it critiques and may send the agent back.
        graph.add_node("reflect", reflect_node)  # type: ignore[arg-type]
        graph.add_conditional_edges("agent", _should_continue, {"tools": "tools", END: "reflect"})
        graph.add_conditional_edges("reflect", _after_reflect, {"agent": "agent", END: end_target})
    else:
        graph.add_conditional_edges("agent", _should_continue, {"tools": "tools", END: end_target})
    graph.add_edge("tools", "agent")
    return graph


def _after_reflect(state: AgentState) -> Literal["agent", "__end__"]:
    """Route out of the ``reflect`` node — ``revise`` loops back to the
    agent, ``accept`` (and budget-exhausted) ends the run."""
    reflections = state.get("reflections", [])
    if reflections and reflections[-1].verdict == "revise":
        return "agent"
    return "__end__"


def _merge_into_system(messages: list[BaseMessage], block: str) -> list[BaseMessage]:
    """Return a new message list with ``block`` appended to the leading
    system message (or a fresh system message prepended).

    The checkpointed ``state['messages']`` is left untouched — the
    injected context rides only in this per-call prompt.
    """
    if messages and isinstance(messages[0], SystemMessage):
        head = messages[0]
        head_text = head.content if isinstance(head.content, str) else str(head.content)
        return [SystemMessage(content=f"{head_text}\n\n{block}"), *messages[1:]]
    return [SystemMessage(content=block), *messages]


def _inject_plan(messages: list[BaseMessage], plan: Plan) -> list[BaseMessage]:
    """Render the plan (J.1) into the prompt's system context."""
    return _merge_into_system(messages, render_plan(plan))


def _inject_memories(messages: list[BaseMessage], memories: list[MemoryItem]) -> list[BaseMessage]:
    """Render recalled long-term memories (J.3) into the system context."""
    lines = ["## Relevant memories from past sessions"]
    lines.extend(f"- ({item.kind}) {item.content}" for item in memories)
    return _merge_into_system(messages, "\n".join(lines))


def _build_mutation_advisory(failed: list[MutationOutcome]) -> HumanMessage:
    """Stream L.L4 — render a ``<mutation-advisory>`` HumanMessage from
    the list of file mutations that did NOT land in the previous tools
    batch (Mini-ADR L-4).

    The wire shape matches Hermes ``conversation_loop.py:3916-3939``:
    a single bracketed advisory listing tool name + path + error, so
    the model cannot claim success on those paths in the next response.
    Lives as a HumanMessage (not SystemMessage) so the L1 prompt-cache
    prefix invariant — ``system`` is build-once / replay-verbatim —
    stays intact.
    """
    lines = [
        "<mutation-advisory>",
        "The following file mutations from the previous tool batch did NOT land. "
        "DO NOT assume these paths have the requested content; retry or surface "
        "the failure to the user.",
    ]
    for outcome in failed:
        line = f"- {outcome.tool_name} path={outcome.path}"
        if outcome.error:
            line += f": {outcome.error}"
        lines.append(line)
    lines.append("</mutation-advisory>")
    return HumanMessage(content="\n".join(lines))


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
) -> tuple[ToolMessage, Mapping[str, Any], int]:
    """Dispatch one tool call.

    Returns ``(tool_message, state_updates, refund_iterations)`` so the
    surrounding tools node can promote allowlisted ``state_updates``
    keys (Stream K.K8) into the ``AgentState`` update dict and
    accumulate ``refund_iterations`` (Stream L.L5) for the next agent
    node to consume. ``state_updates`` is empty and refund is ``0``
    for every code path that does not produce a successful
    :class:`~orchestrator.tools.registry.ToolResult` (errors, blocks,
    unknown tools).
    """
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
        return (
            ToolMessage(
                content=_format_error(exc),
                tool_call_id=call_id,
                status="error",
            ),
            {},
            0,
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
        return (
            ToolMessage(
                content=_format_error(exc),
                tool_call_id=call_id,
                status="error",
            ),
            {},
            0,
        )


def _build_tool_context(config: RunnableConfig, *, plan: Plan | None = None) -> ToolContext:
    """Lift tenant / user binding out of ``config["configurable"]`` into
    a :class:`ToolContext`. Missing values fall through as ``None`` —
    M0 dev / unit tests rarely supply tenant_id, and per-tenant tools
    (E.8 HTTP, E.9 MCP) handle the ``None`` case explicitly (deny-all).

    The run's :class:`CancellationToken` is threaded through too (Stream
    J.4) — ``cancellation_token`` returns a fresh, never-cancelled token
    when the config carries none, so the field is always populated.

    ``plan`` (Stream K.K8) carries the current ``AgentState.plan`` so the
    ``update_plan`` builtin can keep the original goal when revising
    steps. ``None`` for react-mode runs.
    """
    configurable = config.get("configurable") or {}
    tenant_id = _parse_uuid(configurable.get("tenant_id"))
    run_id = _parse_uuid(configurable.get("run_id"))
    user_id = _parse_uuid(configurable.get("user_id"))
    return ToolContext(
        tenant_id=tenant_id,
        run_id=run_id,
        user_id=user_id,
        cancellation_token=cancellation_token(config),
        plan=plan,
    )


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
) -> tuple[ToolMessage, Mapping[str, Any], int]:
    try:
        result = await tool.call(args, ctx=ctx)
    except Exception as exc:
        logger.warning(
            "tools.dispatch_failed name=%s call_id=%s err=%s",
            tool.spec.name,
            call_id,
            type(exc).__name__,
        )
        return (
            ToolMessage(
                content=_format_error(exc),
                tool_call_id=call_id,
                status="error",
            ),
            {},
            0,
        )
    return (
        ToolMessage(content=result.content, tool_call_id=call_id),
        result.state_updates,
        result.refund_iterations,
    )


def _format_error(exc: BaseException) -> str:
    summary = str(exc)
    if len(summary) > _ERROR_SUMMARY_MAX_CHARS:
        summary = summary[:_ERROR_SUMMARY_MAX_CHARS] + "...[truncated]"
    return f"[tool error] {type(exc).__name__}: {summary}"
