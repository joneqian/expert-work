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
  a pre-dispatch middleware may raise to block the dispatch.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any, Literal, cast
from uuid import UUID

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from helix_agent.common.dlp import scan_and_redact
from helix_agent.common.observability import (
    HelixComponent,
    helix_counter,
    helix_gauge,
    helix_histogram,
    helix_span,
)
from helix_agent.common.output_screen import REFUSAL_TEXT, screen_output
from helix_agent.common.spotlight import spotlight_untrusted
from helix_agent.common.uplift_metrics import record_memory_inject_mode
from helix_agent.protocol import AuditAction, AuditEntry, AuditResult, MemoryItem, Plan
from helix_agent.runtime.audit.logger import AuditLogger
from helix_agent.runtime.cancellation import CancellationToken, RunCancelledError
from helix_agent.runtime.middleware import (
    MiddlewareChain,
    MiddlewareContext,
)
from orchestrator.context import (
    ContextCompressor,
    PreCompactionHook,
    ProjectionResult,
    ToolResultPruner,
    WorkingWindow,
    WorkspaceFileWriter,
    WorkspaceProjector,
)
from orchestrator.errors import MaxStepsExceededError
from orchestrator.graph_builder._approval import (
    ApprovalTarget,
    apply_resume_decision,
    build_approval_request,
    find_approval_target,
)
from orchestrator.graph_builder._config import audit_logger_from_config, cancellation_token
from orchestrator.graph_builder.memory import MemoryNode, PreCompactionFlush
from orchestrator.graph_builder.planner import PlannerNode, render_plan
from orchestrator.graph_builder.reflect import ReflectNode
from orchestrator.llm import LLMCaller
from orchestrator.output_judge import ActionJudge, OutputJudge
from orchestrator.state import AgentState
from orchestrator.tools.error_classifier import (
    ClassifiedToolError,
    classified_invalid_arguments,
    classified_mutation_not_landed,
    classify_tool_error,
    render_recovery_advisory,
)
from orchestrator.tools.find_tools import promotion_events
from orchestrator.tools.mutation_classifier import classify as classify_mutation
from orchestrator.tools.overflow import (
    EXEMPT_TOOLS,
    EXTERNALIZE_MIN_CHARS,
    clamp_overflow,
    fallback_truncate,
    make_preview,
    overflow_rel_path,
    render_overflow_footer,
)
from orchestrator.tools.registry import (
    TOOL_ALLOWED_STATE_KEYS,
    Tool,
    ToolContext,
    ToolNotFoundError,
    ToolRegistry,
    ToolResult,
)
from orchestrator.tools.scheduling import MAX_TOOL_WORKERS, plan_stages

logger = logging.getLogger(__name__)

#: Stream HX-12 (Mini-ADR HX-I5) — a promoted tool unused for this many
#: ReAct steps is dropped from the bind when the compressor fires. Constant
#: by design (the HX-6 EWMA discipline: parameterize when hit-rate data
#: demands it); a demoted tool stays re-promotable from the deferred pool.
_PROMOTED_STALE_STEPS = 12

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

# Stream TE-3 — per-tool observability. ``outcome`` is one of ``ok`` (tool
# returned a non-error result), ``error`` (tool raised / returned an error /
# unknown tool), or ``blocked`` (a pre-dispatch middleware refused the call).
# A separate ``helix_tool_error_total`` would be redundant: errors are exactly
# ``helix_tool_call_total{outcome="error"}`` + ``{outcome="blocked"}``.
# Cardinality: ``outcome`` is a fixed 3-value set; the ``tool`` label is
# normalised by ``_metric_tool_label`` so externally-defined MCP tool names
# (``mcp:<server>.<tool>`` — a single server can expose dozens) collapse to
# ``mcp:<server>`` and never blow up the series count. tenant / call_id are
# deliberately omitted — those unbounded identifiers live in the TE-2 audit row.
_tool_call_total = helix_counter(
    "helix_tool_call_total",
    "Tool dispatches by tool name and outcome (ok / error / blocked).",
    ("tool", "outcome"),
)
_tool_latency_seconds = helix_histogram(
    "helix_tool_latency_seconds",
    "Wall-clock seconds per tool dispatch, labelled by tool name.",
    ("tool",),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)
#: Stream CM-0 — DB→/workspace projections per turn, by outcome
#: (projected = files written / skipped = unchanged / error = best-effort fail).
_cm_projection_total = helix_counter(
    "helix_cm_projection_total",
    "Workspace state projections at the turn boundary (Stream CM-0).",
    ("outcome",),
)
#: Stream CM-0 (N1) — size (chars) of the most recent plan recitation injected
#: into the prompt tail. Watches for plan-recitation bloat in long runs.
_cm_recitation_chars = helix_gauge(
    "helix_cm_recitation_chars",
    "Characters of the plan recitation injected into the prompt tail (Stream CM-0 N1).",
)
#: Stream CM-1 — failed tool calls by error class and tool name, fed into
#: the ``<recovery-advisory>`` channel. Raw success/error counts stay on
#: ``helix_tool_call_total{outcome}``; this adds the recovery taxonomy.
_cm_tool_error_total = helix_counter(
    "helix_cm_tool_error_total",
    "Classified tool failures routed into the recovery advisory (Stream CM-1).",
    ("error_class", "tool"),
)
#: Stream CM-1 — size (chars) of the most recent recovery advisory
#: injected into the prompt tail. Watches for advisory bloat.
_cm_recovery_advisory_chars = helix_gauge(
    "helix_cm_recovery_advisory_chars",
    "Characters of the recovery advisory injected into the prompt tail (Stream CM-1).",
)
#: Stream CM-2 — working-memory sliding window passes by outcome. A high
#: ``trimmed`` rate vs ``noop`` shows how many compressor (LLM) calls the
#: cheap gate spared. ``noop`` covers under-threshold + nothing-to-cut.
_cm_working_window_total = helix_counter(
    "helix_cm_working_window_trim_total",
    "Working-memory sliding-window passes at the agent_node entry (Stream CM-2).",
    ("outcome",),
)
#: Stream CM-2 — user turns dropped by the most recent window trim (0 when
#: the pass was a no-op). Watches trim depth on long conversations.
_cm_working_window_dropped_turns = helix_gauge(
    "helix_cm_working_window_dropped_turns",
    "User turns dropped by the most recent working-memory window trim (Stream CM-2).",
)
#: Stream CM-3 — pre-compaction flush passes by outcome. ``flushed`` =
#: memories written from the discarded middle; ``empty`` = nothing
#: extracted (or a swallowed best-effort failure — see memory.flush logs).
_cm_precompaction_flush_total = helix_counter(
    "helix_cm_precompaction_flush_total",
    "Pre-compaction memory flushes before a compressor pass (Stream CM-3).",
    ("outcome",),
)
#: Stream CM-3 — memories written by the most recent pre-compaction flush.
_cm_precompaction_flush_memories = helix_gauge(
    "helix_cm_precompaction_flush_memories",
    "Memories written by the most recent pre-compaction flush (Stream CM-3).",
)
#: Stream CM-5 — oversized tool results externalized to the workspace
#: (externalized = full output saved + reference footer appended /
#: degraded = write failed, the truncated content stands alone).
_cm_tool_overflow_total = helix_counter(
    "helix_cm_tool_overflow_total",
    "Tool-result overflow externalizations (Stream CM-5).",
    ("outcome", "tool"),
)
#: Stream CM-5 — size (chars) of the most recent externalized overflow.
_cm_tool_overflow_chars = helix_gauge(
    "helix_cm_tool_overflow_chars",
    "Characters of the most recent externalized tool-result overflow (Stream CM-5).",
)
#: Stream CM-9 — limit-hit effort escalations by signal (loop = the
#: loop-detection middleware tripped last turn; budget = step_count
#: crossed 75% of max_steps).
_cm_effort_escalation_total = helix_counter(
    "helix_cm_effort_escalation_total",
    "Turns served by the escalated higher-effort caller (Stream CM-9).",
    ("signal",),
)
# Stream PI-2 — model responses blocked by output screening, by the violation
# category that fired (``secret`` / ``exfil_url`` / ``canary``). A non-zero
# rate here is the inline-injection backstop catching a leak the model emitted.
_output_screen_blocked_total = helix_counter(
    "helix_output_screen_blocked_total",
    "Model responses blocked by PI-2 output screening, by violation category.",
    ("category",),
)
# Stream PI-2b — output-judge rulings by verdict (``aligned`` / ``misaligned``
# / ``leak`` / ``error``). ``misaligned`` + ``leak`` are blocks; ``error`` is a
# judge failure routed through the configured fail-open / fail-closed policy.
_output_judge_total = helix_counter(
    "helix_output_judge_total",
    "PI-2b output-judge rulings by verdict (aligned/misaligned/leak/error).",
    ("verdict",),
)
# Stream PI-3b — action-judge rulings on proposed tool calls
# (``aligned`` / ``misaligned`` / ``error``). A misaligned call is denied
# (block mode) or routed to the approval gate (approval mode).
_action_screen_total = helix_counter(
    "helix_action_screen_total",
    "PI-3b action-judge rulings on tool calls (aligned/misaligned/error).",
    ("verdict",),
)
# Stream 7.4 — outbound DLP redactions on terminal responses, by PII category
# (``email`` / ``phone_cn`` / ``id_card_cn`` / ``credit_card``). Conditional
# output: the reply is redacted in place, not blocked.
_output_dlp_redacted_total = helix_counter(
    "helix_output_dlp_redacted_total",
    "Outbound DLP redactions on terminal responses, by PII category.",
    ("category",),
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
    # Stream CM-0 PR2b — run-start file→DB ingest of a human-edited PLAN.md.
    workspace_ingest_node: MemoryNode | None = None,
    escalated_llm_caller: LLMCaller | None = None,
    before_llm_chain: MiddlewareChain | None = None,
    after_llm_chain: MiddlewareChain | None = None,
    before_tool_dispatch_chain: MiddlewareChain | None = None,
    context_compressor: ContextCompressor | None = None,
    # Stream CM-2 — working-memory sliding window: cheap LLM-free turn-trim
    # gate run before the compressor at the agent_node entry. ``None`` →
    # no pre-compressor trimming (the default; unchanged from pre-CM-2).
    working_window: WorkingWindow | None = None,
    # Stream CM-12 — mechanical tool-result prune gate: the cheapest, least-lossy
    # gate, run BEFORE the working window at the agent_node entry. Collapses old
    # tool results to 1-line references (lossless for Phase-1-externalized ones).
    # ``None`` → no prune (the default; unchanged from pre-CM-12).
    tool_result_pruner: ToolResultPruner | None = None,
    # Stream CM-3 — pre-compaction flush: when set, agent_node hands the
    # compressor a callback that flushes the about-to-be-discarded middle
    # to long-term memory before each pass summarises it away. ``None`` →
    # no flush (the default; unchanged from pre-CM-3).
    pre_compaction_flush: PreCompactionFlush | None = None,
    # Stream CM-0 — builds a per-turn ``WorkspaceFileWriter`` bound to the
    # run's ToolContext (the real one rides the warm sandbox). ``None`` →
    # no state projection (the default; the unit-test / no-sandbox path).
    workspace_writer_factory: Callable[[ToolContext], WorkspaceFileWriter] | None = None,
    approval_required_tools: frozenset[str] = frozenset(),
    approval_timeout_s: int = 86400,
    # Stream HX-13 — vendor-native tool-disclosure tier from the model
    # catalog (``ModelEntry.tool_disclosure``). ``None`` (default) keeps the
    # HX-12 application tier byte-identical; "native_search" hands the
    # deferred pool to Anthropic's server-side tool search (find_tools
    # excluded); "allowed_tools" freezes the full schema set on the wire
    # and drives the OpenAI allowed subset via promotion.
    tool_disclosure: Literal["native_search", "allowed_tools"] | None = None,
    # Capability Uplift Sprint #8 — Mini-ADR U-8.
    memory_recall_mode: Literal["per_session", "per_turn"] = "per_session",
    # Stream PI-1b — when set, untrusted channels (recalled memory, tool
    # results) are spotlighted (datamarked + nonce-fenced) before the model
    # sees them. ``None`` (default) keeps the pre-PI behaviour byte-identical.
    spotlight_nonce: str | None = None,
    # Stream PI-2 — when True, each model response is screened for credential
    # leaks / data-exfil forms and a hit is replaced with a refusal (the
    # inline-injection backstop). ``False`` (default) keeps the path unchanged.
    output_screen: bool = False,
    # Stream PI-2b — the model-backed judge escalation above the rule screen.
    # When set, terminal responses the rules didn't already block are judged for
    # alignment / leakage and a block is replaced with a refusal. ``None``
    # (default) keeps the judge tier inert. ``output_judge_on_error`` picks the
    # fail-open (default) vs fail-closed degradation when the judge call fails.
    output_judge: OutputJudge | None = None,
    output_judge_on_error: Literal["open", "closed"] = "open",
    # Stream PI-3b — when set + ``action_screen`` is on, each proposed tool
    # call is judged for alignment before dispatch; a misaligned turn is denied
    # ("block") or routed to the approval gate ("approval"). ``None`` / "off"
    # keeps the dispatch path unchanged.
    action_judge: ActionJudge | None = None,
    action_screen: Literal["off", "block", "approval"] = "off",
    action_screen_on_error: Literal["open", "closed"] = "open",
    # Stream 7.4 — when True, each terminal response (no tool_calls) is scanned
    # for PII (email / phone / national id / payment card) and matches are
    # redacted in place before the reply leaves. ``False`` (default) keeps the
    # path unchanged. Conditional output: redacts, never blocks.
    output_dlp: bool = False,
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

    # Stream TE-4 — side-effect-driven approval gating. Tools that declare
    # ``side_effect="irreversible"`` (resolved via ToolSpec) are auto-gated:
    # union them into the manifest's ``approval_required_tools`` so the
    # approval gate fires on them without each manifest having to list them.
    # Computed once at build time (the registry is fixed for the agent's
    # life). Zero behaviour change until a tool actually declares
    # irreversible — no builtin does yet; ``bash`` (TE-5) is the first.
    # Stream TE-6 — classify over ``all_specs()`` (active + deferred) so a
    # deferred irreversible tool stays gated once ``find_tools`` promotes it.
    # Equals ``specs()`` when nothing is deferred.
    _irreversible_tools = frozenset(
        spec.name
        for spec in tool_registry.all_specs()
        if spec.resolved_side_effect == "irreversible"
    )
    _gated_tools = approval_required_tools | _irreversible_tools

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

        # Stream TE-6 — bind active specs plus any deferred tools the run has
        # promoted via ``find_tools`` (carried per-thread on AgentState, so the
        # cached registry stays untouched). ``deferred_specs([])`` is empty when
        # nothing was promoted → identical to the pre-TE-6 ``specs()`` bind.
        # Stream HX-13 — the vendor-native tiers reshape this bind; the
        # ``None`` tier is the HX-12 application tier, byte-identical.
        promoted = state.get("promoted_tools") or []
        if tool_disclosure == "native_search":
            # Anthropic server-side tool search: every still-deferred tool
            # rides along marked ``defer_loading`` (the API retrieves and
            # invokes it; HX-12 call-through promotes on dispatch), and our
            # own ``find_tools`` leaves the bind — one retrieval channel
            # only (Mini-ADR HX-J3).
            promoted_set = set(promoted)
            active = [s for s in tool_registry.specs() if s.name != "find_tools"]
            still_deferred = [
                replace(s, defer_loading=True)
                for s in tool_registry.deferred_specs(tool_registry.deferred_names())
                if s.name not in promoted_set
            ]
            tools = [*active, *tool_registry.deferred_specs(promoted), *still_deferred]
        elif tool_disclosure == "allowed_tools":
            # OpenAI/Azure: the FULL schema set goes on the wire every turn
            # (prompt-cache friendly); still-deferred tools carry the marker
            # so the adapter excludes them from ``tool_choice.allowed_tools``.
            # ``find_tools`` stays — under the allowed constraint it is the
            # only promotion entry point (Mini-ADR HX-J3).
            promoted_set = set(promoted)
            still_deferred = [
                replace(s, defer_loading=True)
                for s in tool_registry.deferred_specs(tool_registry.deferred_names())
                if s.name not in promoted_set
            ]
            tools = [
                *tool_registry.specs(),
                *tool_registry.deferred_specs(promoted),
                *still_deferred,
            ]
        else:
            tools = [*tool_registry.specs(), *tool_registry.deferred_specs(promoted)]
        messages = list(state["messages"])
        # Stream CM-12 — mechanical tool-result prune: the cheapest, least-lossy
        # gate, run FIRST. When over threshold it collapses OLD tool results
        # (beyond the most-recent N) to 1-line references — lossless for
        # Phase-1-externalized results (full output on disk under .tool_results/),
        # a short stub otherwise — while keeping every turn + the assistant's
        # reasoning intact. Running it before the window means the coarser gates
        # re-estimate against a smaller prompt and fire less often. Prompt-view
        # only — the checkpointed history is never rewritten (CM-C4).
        if tool_result_pruner is not None:
            messages = tool_result_pruner.apply(messages).messages
        # Stream CM-2 — working-memory sliding window: cheap LLM-free first
        # gate. Trims the raw history to first turn + most-recent N turns
        # when over threshold (on HumanMessage boundaries, so tool-call
        # pairs stay intact), BEFORE plan/memory/advisory injection (those
        # are this turn's guidance and must always reach the LLM) and the
        # compressor preflight (the second, LLM-backed gate). Trims only
        # this prompt view — the checkpointed history is never rewritten.
        if working_window is not None:
            trim = working_window.apply(messages)
            messages = trim.messages
            _cm_working_window_total.labels(
                outcome="trimmed" if trim.dropped_turns else "noop"
            ).inc()
            _cm_working_window_dropped_turns.set(trim.dropped_turns)
        # Stream J.1 — render the plan into the system context so every
        # ReAct step executes against it. No-op for plain ReAct graphs.
        plan = state.get("plan")
        if plan is not None:
            messages = _inject_plan(messages, plan)
        # Stream J.3 — render recalled long-term memories into context.
        # Capability Uplift Sprint #8 (Mini-ADR U-8) — ``memory_recall_mode``
        # decides where the block lands: ``per_session`` (default) anchors
        # it at the prefix slot ``messages[1]`` so the Anthropic adapter
        # can mark it with ``cache_control`` and the prompt cache covers
        # ``[system, task, memories]`` across all turns. ``per_turn`` keeps
        # the J.3 tail-injection behavior as a legacy escape hatch.
        memories = state.get("recalled_memories")
        if memories:
            messages = _inject_memories(
                messages, memories, mode=memory_recall_mode, spotlight_nonce=spotlight_nonce
            )
            record_memory_inject_mode(mode=memory_recall_mode)
        # Stream CM-1 (generalising L.L4) — inject a ``<recovery-advisory>``
        # HumanMessage listing every tool call that failed in the previous
        # tools batch, with grounded per-tool recovery guidance. Mini-ADR
        # CM-B4: the advisory is part of the conversation history (persists
        # across turns) and lives in a HumanMessage, NOT the system block,
        # so the L1 prompt-cache prefix invariant stays intact. Append once
        # per failure batch — the channel is reset to ``[]`` in this node's
        # return dict so a follow-on agent step does not double-inject.
        tool_failures = list(state.get("tool_failures", []))
        advisory_message: HumanMessage | None = None
        if tool_failures:
            advisory_message = _build_recovery_advisory(tool_failures)
            messages = [*messages, advisory_message]
            _cm_recovery_advisory_chars.set(len(str(advisory_message.content)))
        # Stream L.L2 — token preflight + summarise-the-middle. When
        # the prompt would exceed the model's configured threshold the
        # compressor swaps the conversation's middle for a
        # ``<context-summary>`` system message, keeping head + tail
        # intact. Mini-ADR L-2: a ContextOverflowError here surfaces
        # as a run failure (no silent fallback) so the orchestrator
        # can write a clean RUN_FAILED audit row.
        demoted_tools: list[str] = []
        if context_compressor is not None and context_compressor.should_compress(messages):
            # Stream CM-3 — bind a config-scoped flush so the compressor can
            # hand the middle to long-term memory before discarding it. The
            # callback is best-effort (the flusher swallows its own non-cancel
            # failures); cancellation still propagates out and aborts the run.
            on_pre_compaction: PreCompactionHook | None = None
            if pre_compaction_flush is not None:
                flush_cb = pre_compaction_flush

                async def _on_pre_compaction(middle: Sequence[BaseMessage]) -> None:
                    written = await flush_cb(middle, config, token)
                    _cm_precompaction_flush_total.labels(
                        outcome="flushed" if written else "empty"
                    ).inc()
                    _cm_precompaction_flush_memories.set(written)

                on_pre_compaction = _on_pre_compaction
            messages = await context_compressor.compress(
                messages, on_pre_compaction=on_pre_compaction
            )
            # Stream HX-12 (Mini-ADR HX-I5) — promotion demotion rides the
            # same pressure signal: the context is being squeezed, so
            # promoted tools unused for N turns leave the next turn's bind.
            # They stay in the deferred pool — find_tools / a direct
            # call-through re-promotes any of them at any time, so this
            # only slims the bind, never loses a capability. Manifest
            # (core/active) tools are structurally out of scope: demotion
            # touches only the promoted-from-deferred list.
            last_used = state.get("promoted_tool_last_used") or {}
            demoted_tools = [
                name
                for name in promoted
                # No stamp = freshly promoted this very turn; never stale.
                if step_count - last_used.get(name, step_count) > _PROMOTED_STALE_STEPS
            ]
            if demoted_tools:
                promotion_events.labels(event="demote").inc(len(demoted_tools))
                logger.info(
                    "tools.promotion_demoted count=%d step=%d", len(demoted_tools), step_count
                )
        configurable = config.get("configurable") or {}
        tenant_id = _parse_uuid(configurable.get("tenant_id"))
        # Stream Agent-Templates (M1-5a) — the end-user this run is for, threaded
        # to the token-usage middleware for per-user cost attribution.
        user_id = _parse_uuid(configurable.get("user_id"))

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

        # Stream CM-9 (Mini-ADR CM-J5) — limit-hit escalation: serve this
        # turn from the higher-effort caller when the loop-detection
        # middleware tripped last turn, or the step budget is nearly
        # spent (one deliberate deep think beats more shallow retries).
        # Request params only — the prompt bytes are unchanged, so the
        # provider prompt cache is unaffected.
        loop_signal = bool(state.get("escalate_next"))
        budget_signal = max_steps > 0 and step_count * 4 >= max_steps * 3
        # Stream CM-11 (Mini-ADR CM-M1) — event-driven escalation, the second
        # of the two dynamic-compute triggers:
        #  * micro: a non-transient tool failure in the previous batch is a
        #    real anomaly to reason through (``transient`` is retryable
        #    jitter, not worth a deep think). The recovery advisory for these
        #    failures lands this same turn, so escalate the turn that reacts.
        #  * macro: the live plan goal changed since the previous turn (a
        #    re-plan, or a human PLAN.md edit ingested via CM-0) — one deep
        #    think to re-calibrate the execution strategy. The initial plan is
        #    NOT a change (the planner already decomposed it deeply), so a
        #    prior goal must exist to diff against.
        error_signal = any(f.error_class != "transient" for f in tool_failures)
        current_goal = plan.goal if plan is not None else None
        prior_goal = state.get("last_plan_goal")
        goal_signal = (
            current_goal is not None and prior_goal is not None and current_goal != prior_goal
        )
        active_caller = llm_caller
        if escalated_llm_caller is not None and (
            loop_signal or budget_signal or error_signal or goal_signal
        ):
            active_caller = escalated_llm_caller
            signal = (
                "loop"
                if loop_signal
                else "budget"
                if budget_signal
                else "error"
                if error_signal
                else "goal"
            )
            _cm_effort_escalation_total.labels(signal=signal).inc()
            logger.info("llm.effort_escalated signal=%s step=%d/%d", signal, step_count, max_steps)

        # ``messages`` is now the exact prompt — the E.13 cache key input.
        if cache_hit_response is not None:
            response: AIMessage = cache_hit_response
        else:
            # Wrap the LLM call so a cancel mid-call interrupts the
            # in-flight await rather than waiting it out (E.15).
            # 10.1 — one ``helix.orchestrator.llm_call`` child span per
            # provider call, attached under the session root span.
            with helix_span(HelixComponent.ORCHESTRATOR, "llm_call"):
                response = await token.run_cancellable(
                    active_caller(messages=messages, tools=tools)
                )

        # Stream PI-2 — output screening backstop. Catch a credential leak /
        # exfil form the model emitted (e.g. driven by an inline injection
        # spotlighting can't wrap) before it reaches the user or a tool.
        rule_blocked = False
        if output_screen:
            screened, screen_cats = _screen_model_response(response)
            rule_blocked = screened is not response
            response = screened
            if screen_cats:
                await _emit_output_guard_audit(
                    audit_logger_from_config(config),
                    tenant_id,
                    action=AuditAction.OUTPUT_SCREEN_BLOCKED,
                    result=AuditResult.DENIED,
                    categories=screen_cats,
                )
        # Stream PI-2b — model-backed judge escalation. Skip when the rules
        # already blocked (save the call) and run only on a terminal response
        # (no tool_calls) — the judge is a per-response LLM call.
        if output_judge is not None and not rule_blocked and not _extract_tool_calls(response):
            response = await _judge_model_response(
                response,
                messages,
                judge=output_judge,
                on_error=output_judge_on_error,
                token=token,
            )
        # Stream 7.4 — outbound DLP. Redact PII the model emitted in a terminal
        # response before it leaves. Skip when the rules already blocked (the
        # refusal carries no PII) and only on a terminal turn (a tool-call turn's
        # args route through the action screen, not here).
        if output_dlp and not rule_blocked and not _extract_tool_calls(response):
            response, dlp_cats = _dlp_redact_response(response)
            if dlp_cats:
                await _emit_output_guard_audit(
                    audit_logger_from_config(config),
                    tenant_id,
                    action=AuditAction.OUTPUT_DLP_REDACTED,
                    result=AuditResult.SUCCESS,
                    categories=dlp_cats,
                )

        if after_llm_chain is not None:
            after_messages: list[BaseMessage] = [*messages, response]
            ctx = MiddlewareContext(
                payload={
                    "messages": after_messages,
                    "response": response,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "prompt_messages": messages,
                    "cache_hit": cache_hit_response is not None,
                }
            )
            await after_llm_chain.invoke(ctx, _noop)
            new_messages = _extract_post_llm_messages(ctx, original=after_messages)
            # Stream CM-1 — persist the advisory into history so the
            # next agent step sees it even after this dict's reducer
            # appends. The middleware path's ``new_messages`` is the
            # full post-LLM delta; prepend the advisory in case the
            # middleware filtered the prompt body.
            persisted_messages: list[BaseMessage] = list(new_messages)
            if advisory_message is not None and advisory_message not in persisted_messages:
                persisted_messages = [advisory_message, *persisted_messages]
            update_mw: dict[str, Any] = {
                "messages": persisted_messages,
                "step_count": step_count + 1,
                "step_count_refund_pending": 0,
                "tool_failures": [],
                # CM-9 — arm escalation for the next step when the loop
                # middleware tripped on THIS response; otherwise reset
                # the consumed signal.
                "escalate_next": bool(ctx.payload.get("loop_detected")),
                # CM-11 — rebaseline the goal so a one-off change escalates
                # exactly one turn (next turn diffs against this value).
                "last_plan_goal": current_goal,
            }
            if demoted_tools:
                update_mw["promoted_tools"] = {"remove": demoted_tools}
            return update_mw

        # Stream CM-1 — persist the advisory in conversation history
        # alongside the LLM response so the next agent step sees it.
        emit_messages: list[BaseMessage] = (
            [advisory_message, response] if advisory_message is not None else [response]
        )
        update_plain: dict[str, Any] = {
            "messages": emit_messages,
            "step_count": step_count + 1,
            "step_count_refund_pending": 0,
            "tool_failures": [],
            "escalate_next": False,  # CM-9 — no middleware chain, reset
            "last_plan_goal": current_goal,  # CM-11 — rebaseline the goal
        }
        if demoted_tools:
            update_plain["promoted_tools"] = {"remove": demoted_tools}
        return update_plain

    async def tools_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        token = cancellation_token(config)
        token.raise_if_cancelled()

        last = state["messages"][-1]
        tool_calls = _extract_tool_calls(last)
        if not tool_calls:
            return {}

        # Stream J.8 (Mini-ADR J-24) — approval gate. Two re-entrant paths:
        #
        # 1. RESUME — ``approval_resume`` set: a human verdict came back
        #    via ``aupdate_state``. Apply it (approve dispatches, modify
        #    rewrites args, reject synthesises rejection ToolMessages)
        #    and clear the channel. Skips re-detection so the gate does
        #    not re-fire on the same turn.
        # 2. DETECT — no resume in flight: scan for the first gated call
        #    (a tool in ``_gated_tools`` = manifest ``approval_required_tools``
        #    plus TE-4 irreversible tools, or ``ask_for_approval``).
        #    On a hit, write ``pending_approval`` + dispatch nothing —
        #    ``_after_tools`` routes to END (RunStatus.PAUSED). The
        #    end-and-resume model (vs LangGraph ``interrupt()``) keeps
        #    the parallel L.L6 staging below untouched.
        approval_resume = state.get("approval_resume")
        ingest_update: dict[str, Any] = {}
        if approval_resume is not None:
            # Stream CM-8 (Mini-ADR CM-I4) — the resume re-entry skips the
            # entry chain (``aupdate_state(as_node="agent")`` lands the
            # graph straight here), so run the workspace ingest now: a
            # PLAN.md edited during the pause still flows back before the
            # verdict executes. Best-effort + strict-scan semantics live
            # inside the node (CM-0, unchanged).
            if workspace_ingest_node is not None:
                ingest_update = await workspace_ingest_node(state, config)
            resume_outcome = apply_resume_decision(tool_calls, _gated_tools, approval_resume)
            if resume_outcome.reject_messages:
                rejected: dict[str, Any] = {
                    **ingest_update,
                    "messages": list(resume_outcome.reject_messages),
                    "approval_resume": None,
                }
                if resume_outcome.terminal:
                    rejected["approval_outcome"] = "rejected"
                return rejected
            # approve / modify — fall through to dispatch the (possibly
            # arg-rewritten) calls; clear the resume channel on return.
            tool_calls = resume_outcome.tool_calls
        elif not state.get("pending_approval"):
            # Stream PI-3b — action screening: judge each proposed tool call
            # against the user's request before dispatch. A misaligned turn is
            # denied (block) or routed to the approval gate (approval).
            if action_screen != "off" and action_judge is not None:
                bad_idx = await _first_misaligned_action(
                    tool_calls,
                    state["messages"],
                    judge=action_judge,
                    on_error=action_screen_on_error,
                    token=token,
                )
                if bad_idx is not None:
                    if action_screen == "approval":
                        configurable = config.get("configurable") or {}
                        thread_id = str(configurable.get("run_id") or "run")
                        return {
                            "pending_approval": build_approval_request(
                                ApprovalTarget(
                                    index=bad_idx,
                                    tool_call=tool_calls[bad_idx],
                                    is_agent_initiated=False,
                                ),
                                thread_id=thread_id,
                                timeout_s=approval_timeout_s,
                            )
                        }
                    # block — deny the whole turn (one error ToolMessage per
                    # call so no tool_call is left orphaned); the agent re-plans.
                    return {
                        "messages": [
                            ToolMessage(
                                content=(
                                    "[blocked] action screening: a tool call did not match "
                                    "your request and was not run"
                                ),
                                tool_call_id=str(call.get("id") or ""),
                                status="error",
                            )
                            for call in tool_calls
                        ]
                    }
            target = find_approval_target(tool_calls, _gated_tools)
            if target is not None:
                configurable = config.get("configurable") or {}
                thread_id = str(configurable.get("run_id") or "run")
                return {
                    "pending_approval": build_approval_request(
                        target,
                        thread_id=thread_id,
                        timeout_s=approval_timeout_s,
                    )
                }

        ctx_obj = _build_tool_context(config, plan=state.get("plan"))
        # Stream TE-2 — per-tool-call audit sink (may be None on the dev /
        # unit-test path; ``_dispatch_tool`` treats the emit as best-effort).
        audit_logger = audit_logger_from_config(config)
        # Stream CM-5 — one per-turn writer for overflow externalization,
        # from the same factory (and gate) as the CM-0 projection.
        overflow_writer = (
            workspace_writer_factory(ctx_obj) if workspace_writer_factory is not None else None
        )
        # Stream L.L6 — group tool_calls into stages of mutually-non-
        # conflicting calls. Within a stage we ``asyncio.gather`` (capped
        # at MAX_TOOL_WORKERS); stages execute sequentially so any
        # state-mutating call (``update_plan``, ``save_artifact`` on a
        # contested path) still observes the LLM's intended ordering.
        # Stream TE-6 — schedule over ``all_specs()`` so a promoted deferred
        # tool is classified (side_effect / path_args) correctly when called.
        # Equals ``specs()`` when nothing is deferred.
        specs_by_name = {spec.name: spec for spec in tool_registry.all_specs()}
        stages = plan_stages(tool_calls, specs_by_name)
        results: dict[
            int, tuple[ToolMessage, Mapping[str, Any], int, ClassifiedToolError | None]
        ] = {}
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
        ) -> tuple[ToolMessage, Mapping[str, Any], int, ClassifiedToolError | None]:
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
                    audit_logger=audit_logger,
                    overflow_writer=overflow_writer,
                    spotlight_nonce=spotlight_nonce,
                )
            )

        semaphore = asyncio.Semaphore(MAX_TOOL_WORKERS)

        async def _bounded(
            tc: dict[str, Any],
        ) -> tuple[ToolMessage, Mapping[str, Any], int, ClassifiedToolError | None]:
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
        # Stream CM-1 (generalising L.L4) — collect classified tool
        # failures so the next agent step injects the recovery advisory.
        # Two sources, in original tool_call order so the advisory lists
        # failures in the sequence the ToolMessages appear:
        #   1. error path — ``_dispatch_tool`` already classified from the
        #      real exception (4th tuple element).
        #   2. success-but-didn't-land — L-4's mutation classifier on a
        #      non-error ToolMessage, folded into ``mutation_not_landed``.
        tool_failures: list[ClassifiedToolError] = []
        for idx in range(len(tool_calls)):
            tool_message, tool_state, refund_inc, classified = results[idx]
            new_messages.append(tool_message)
            for key, value in tool_state.items():
                if key not in TOOL_ALLOWED_STATE_KEYS:
                    continue
                # Stream TE-6 — list-valued channels (``promoted_tools`` union,
                # ``subagent_invocations`` append) must ACCUMULATE within the
                # batch, not overwrite: when several tools write the same
                # channel in one parallel stage (e.g. two ``find_tools`` or two
                # ``is_parallel_safe`` sub-agents), a plain ``[key] = value``
                # keeps only the last call's list and silently drops the rest
                # — the channel reducer runs at the node boundary and never
                # sees the clobbered intra-batch values. Scalar channels
                # (``plan``) keep last-write-wins.
                if isinstance(value, list):
                    existing = accumulated_state.get(key)
                    accumulated_state[key] = (
                        [*existing, *value] if isinstance(existing, list) else list(value)
                    )
                else:
                    accumulated_state[key] = value
            refund_total += refund_inc
            failure = _classify_tool_failure(tool_calls[idx], tool_message, classified)
            if failure is not None:
                tool_failures.append(failure)
                _cm_tool_error_total.labels(
                    error_class=failure.error_class, tool=failure.tool_name
                ).inc()

        # Stream HX-12 — stamp ``promoted_tool_last_used`` for the demotion
        # gate: every already-promoted tool that dispatched in this batch
        # refreshes its stamp; every name freshly promoted in this batch
        # (find_tools result or a call-through) gets its baseline. Tools
        # without a stamp would otherwise be un-ageable.
        current_step = int(state.get("step_count", 0))
        already_promoted = set(state.get("promoted_tools") or [])
        batch_promoted = accumulated_state.get("promoted_tools")
        freshly_promoted = set(batch_promoted) if isinstance(batch_promoted, list) else set()
        used_stamps: dict[str, int] = dict.fromkeys(
            (
                name
                for name in (str(call.get("name", "")) for call in tool_calls)
                if name in already_promoted
            ),
            current_step,
        )
        for name in freshly_promoted:
            used_stamps.setdefault(name, current_step)

        # CM-8 — the resume-path ingest lands first so a tool's own state
        # write (e.g. ``update_plan`` in the resumed batch) still wins.
        result_dict: dict[str, Any] = {
            **ingest_update,
            "messages": new_messages,
            "step_count_refund_pending": refund_total,
            **accumulated_state,
        }
        if used_stamps:
            result_dict["promoted_tool_last_used"] = used_stamps
        # Only write the channel when there are failures — the absent
        # case keeps the agent_node's ``state.get("tool_failures", [])``
        # default fast-path active.
        if tool_failures:
            result_dict["tool_failures"] = tool_failures
        # Stream J.8 — when this batch ran on an approve / modify resume,
        # clear the transient ``approval_resume`` channel so a follow-on
        # turn does not re-apply the stale verdict.
        if approval_resume is not None:
            result_dict["approval_resume"] = None
        # Stream CM-0 — turn-end DB→/workspace projection (best-effort).
        # Only-if-changed: an unchanged turn skips the sandbox round-trip and
        # leaves ``last_projection_hash`` untouched.
        projection = await _project_workspace_state(
            workspace_writer_factory, state, ctx_obj, audit_logger
        )
        if projection is not None and not projection.skipped:
            result_dict["last_projection_hash"] = projection.digest
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
    # Stream CM-0 — file→DB ingest, placed last in the entry chain (after the
    # planner) so a human's PLAN.md edit overrides a (re)generated plan, and so
    # it fires exactly once per ainvoke (run start / resume), not per turn.
    if workspace_ingest_node is not None:
        graph.add_node("workspace_ingest", workspace_ingest_node)  # type: ignore[arg-type]
        entry.append("workspace_ingest")
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
    # Stream J.8 — after ``tools``, a run with ``pending_approval`` set
    # routes straight to END (RunStatus.PAUSED): the checkpoint persists
    # and ``memory_writeback`` is deliberately skipped (the run is paused,
    # not finished). Otherwise the normal ReAct loop continues to ``agent``.
    graph.add_conditional_edges("tools", _after_tools, {"agent": "agent", END: END})
    return graph


def _after_reflect(state: AgentState) -> Literal["agent", "__end__"]:
    """Route out of the ``reflect`` node — ``revise`` loops back to the
    agent, ``accept`` (and budget-exhausted) ends the run."""
    reflections = state.get("reflections", [])
    if reflections and reflections[-1].verdict == "revise":
        return "agent"
    return "__end__"


def _append_tail_human_message(messages: list[BaseMessage], block: str) -> list[BaseMessage]:
    """Stream L.L1 — append per-turn dynamic context as a tail
    ``HumanMessage`` so the leading ``SystemMessage`` stays byte-stable
    across turns (Mini-ADR L-1 — the Anthropic prompt-cache prefix
    invariant).

    The checkpointed ``state['messages']`` is left untouched: the
    injected context rides only in this per-call prompt, the same as
    the pre-L1 ``_merge_into_system`` helper.
    """
    return [*messages, HumanMessage(content=block)]


def _inject_plan(messages: list[BaseMessage], plan: Plan) -> list[BaseMessage]:
    """Render the plan (J.1) into the prompt as a tail HumanMessage.

    Before L1 the plan was concatenated into the leading SystemMessage,
    which would change the cache prefix on every step and disable
    Anthropic prompt caching. L1 moves the per-turn dynamic context
    out of system into a tail HumanMessage so ``system`` stays
    build-once / replay-verbatim.
    """
    rendered = render_plan(plan)
    # Stream CM-0 (N1) — gauge the recitation size to watch for plan bloat.
    _cm_recitation_chars.set(len(rendered))
    return _append_tail_human_message(messages, rendered)


def _inject_memories(
    messages: list[BaseMessage],
    memories: list[MemoryItem],
    *,
    mode: Literal["per_session", "per_turn"] = "per_session",
    spotlight_nonce: str | None = None,
) -> list[BaseMessage]:
    """Render recalled long-term memories (J.3) into the prompt.

    ``mode='per_turn'`` (legacy J.3): append a HumanMessage at the tail
    every turn — same L1 rationale as :func:`_inject_plan`. The memory
    block's position shifts every turn as AI/Tool messages accumulate,
    so the Anthropic prompt cache cannot include it.

    ``mode='per_session'`` (Sprint #8 default, Mini-ADR U-8): insert
    the memory block once at messages position 1 (right after the
    user's task) with ``additional_kwargs["helix_cache_anchor"] = True``
    so the Anthropic adapter (Mini-ADR U-7) marks it with
    ``cache_control: ephemeral``. The prefix
    ``[system_payload, task, memories]`` is then cached across every
    turn of the session — long sessions stop paying full price for the
    memory block on every step.
    """
    # Stream PI-1b — recalled memory is untrusted (an injection can be written
    # into a memory in an earlier session and recalled here). Spotlight the
    # item block (the helix-owned header stays trusted) so the model treats it
    # as data, not instructions.
    items = "\n".join(f"- ({item.kind}) {item.content}" for item in memories)
    if spotlight_nonce:
        items = spotlight_untrusted(items, nonce=spotlight_nonce)
    body = "## Relevant memories from past sessions\n" + items

    if mode == "per_turn":
        return _append_tail_human_message(messages, body)

    # per_session: stable prefix slot + cache anchor metadata. The
    # block lands at position 1 so it sits right after the user task
    # (messages[0] is typically the SystemMessage placeholder for
    # in-graph state, but the provider builds ``system`` separately
    # from its first SystemMessage entry, so ``messages[1]`` is the
    # first non-system slot for downstream content).
    block = HumanMessage(
        content=body,
        additional_kwargs={"helix_cache_anchor": True},
    )
    if not messages:
        return [block]
    return [messages[0], block, *messages[1:]]


def _classify_tool_failure(
    tool_call: dict[str, Any],
    tool_message: ToolMessage,
    classified: ClassifiedToolError | None,
) -> ClassifiedToolError | None:
    """Resolve a single tool call's failure into a classification, if any.

    L-4's mutation classifier wins first (CM-B2): for a known mutation
    tool it carries the more actionable "the write did NOT land — don't
    assume the path has content" guidance + the path, whether the tool
    raised (error-path ``ToolMessage(status="error")``) or returned a
    success-looking message that didn't actually land. Any other failure
    falls back to the error-path ``classified`` from the catch site.
    Returns ``None`` for a genuine success (no mutation gap, no error).
    """
    outcome = classify_mutation(
        str(tool_call.get("name", "")),
        tool_call.get("args") or {},
        tool_message,
    )
    if outcome is not None and not outcome.landed:
        return classified_mutation_not_landed(
            tool_name=outcome.tool_name,
            summary=outcome.error or "mutation did not land",
            path=outcome.path,
        )
    return classified


def _build_recovery_advisory(failures: list[ClassifiedToolError]) -> HumanMessage:
    """Stream CM-1 (generalising L.L4) — render a ``<recovery-advisory>``
    HumanMessage from the classified tool failures of the previous tools
    batch (Mini-ADR CM-B2/CM-B4).

    Generalises L-4's ``<mutation-advisory>`` to every tool failure: each
    line carries the error class + summary + grounded recovery guidance,
    so the model neither claims success on failed calls nor retries them
    blindly. Lives as a HumanMessage (not SystemMessage) so the L1
    prompt-cache prefix invariant — ``system`` is build-once /
    replay-verbatim — stays intact.
    """
    return HumanMessage(content=render_recovery_advisory(failures))


def _should_continue(state: AgentState) -> Literal["tools", "__end__"]:
    last = state["messages"][-1]
    if _extract_tool_calls(last):
        return "tools"
    return "__end__"


def _after_tools(state: AgentState) -> Literal["agent", "__end__"]:
    """Route out of ``tools`` — Stream J.8.

    Ends the run (→ END) in two cases:

    * ``pending_approval`` set — the run paused at an approval gate
      (RunStatus.PAUSED); the checkpoint is what the resume endpoint
      re-invokes from.
    * ``approval_outcome == "rejected"`` — a declarative-gate reject
      vetoed the run; it terminates rather than looping back.

    A normal tools batch (and an agent-initiated ask_for_approval
    reject) loops back to ``agent``.
    """
    if state.get("pending_approval") or state.get("approval_outcome") == "rejected":
        return "__end__"
    return "agent"


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


def _screen_model_response(response: AIMessage) -> tuple[AIMessage, tuple[str, ...]]:
    """Stream PI-2 — screen a model response; refuse a flagged one.

    Returns ``(response, ())`` when clean, else ``(refusal, categories)`` where
    the refusal is a fresh :class:`AIMessage` carrying **no tool_calls** (a
    blocked response must terminate the turn rather than proceed to a
    possibly-injected tool call) and ``categories`` are the fired categories for
    the audit row (audit-eval Phase 4). The matched value is never logged.
    """
    verdict = screen_output(str(response.content))
    if not verdict.blocked:
        return response, ()
    for category in verdict.categories:
        _output_screen_blocked_total.labels(category=category).inc()
    logger.warning("output_screen.blocked categories=%s", ",".join(verdict.categories))
    return AIMessage(content=REFUSAL_TEXT), tuple(verdict.categories)


def _dlp_redact_response(response: AIMessage) -> tuple[AIMessage, tuple[str, ...]]:
    """Stream 7.4 — redact PII in a terminal response (conditional output).

    Returns ``(response, ())`` when no PII matched, else ``(copy, categories)``
    with the matched spans replaced by ``[redacted]`` and the fired categories
    for the audit row (audit-eval Phase 4). Only string content is scanned
    (multimodal content blocks pass through, M2/M3 scope). The matched value is
    never logged — only the category that fired.
    """
    content = response.content
    if not isinstance(content, str):
        return response, ()
    result = scan_and_redact(content)
    if not result.changed:
        return response, ()
    for category in result.categories:
        _output_dlp_redacted_total.labels(category=category).inc()
    logger.info("output_dlp.redacted categories=%s", ",".join(result.categories))
    return response.model_copy(update={"content": result.redacted}), tuple(result.categories)


def _latest_human_text(messages: Sequence[BaseMessage]) -> str:
    """The most recent user-message text — the judge's alignment baseline."""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return str(msg.content)
    return ""


async def _judge_model_response(
    response: AIMessage,
    messages: Sequence[BaseMessage],
    *,
    judge: OutputJudge,
    on_error: Literal["open", "closed"],
    token: CancellationToken,
) -> AIMessage:
    """Stream PI-2b — judge a terminal response for alignment / leakage.

    Returns ``response`` when the judge clears it, else a refusal. A judge
    failure (timeout / outage) routes through ``on_error``: ``"open"`` lets the
    response through (best-effort backstop), ``"closed"`` blocks. The reason is
    logged at category level only — never the response text or any secret.
    """
    try:
        verdict = await token.run_cancellable(
            judge.judge(
                user_request=_latest_human_text(messages),
                response=str(response.content),
                context_hint=None,
            )
        )
    except Exception:
        _output_judge_total.labels(verdict="error").inc()
        if on_error == "closed":
            logger.warning("output_judge.failed policy=fail-closed -> blocking")
            return AIMessage(content=REFUSAL_TEXT)
        logger.warning("output_judge.failed policy=fail-open -> allowing")
        return response
    if verdict.blocked:
        label = "leak" if verdict.leak_suspected else "misaligned"
        _output_judge_total.labels(verdict=label).inc()
        logger.warning("output_judge.blocked verdict=%s reason=%s", label, verdict.reason)
        return AIMessage(content=REFUSAL_TEXT)
    _output_judge_total.labels(verdict="aligned").inc()
    return response


async def _first_misaligned_action(
    tool_calls: list[dict[str, Any]],
    messages: Sequence[BaseMessage],
    *,
    judge: ActionJudge,
    on_error: Literal["open", "closed"],
    token: CancellationToken,
) -> int | None:
    """Stream PI-3b — judge every proposed tool call; return the index of the
    first misaligned one (or ``None`` when all align).

    Records a per-call ``aligned`` / ``misaligned`` / ``error`` metric. A judge
    failure routes through ``on_error``: ``"open"`` treats the call as aligned
    (best-effort backstop), ``"closed"`` treats it as misaligned. Never logs
    the args.
    """
    user_request = _latest_human_text(messages)
    first_bad: int | None = None
    for index, call in enumerate(tool_calls):
        name = str(call.get("name", ""))
        args = call.get("args") or {}
        try:
            verdict = await token.run_cancellable(
                judge.judge_action(user_request=user_request, tool_name=name, tool_args=args)
            )
        except Exception:
            _action_screen_total.labels(verdict="error").inc()
            bad = on_error == "closed"
        else:
            bad = verdict.blocked
            _action_screen_total.labels(verdict="misaligned" if bad else "aligned").inc()
        if bad and first_bad is None:
            first_bad = index
    return first_bad


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
    audit_logger: AuditLogger | None = None,
    overflow_writer: WorkspaceFileWriter | None = None,
    spotlight_nonce: str | None = None,
) -> tuple[ToolMessage, Mapping[str, Any], int, ClassifiedToolError | None]:
    """Dispatch one tool call.

    Returns ``(tool_message, state_updates, refund_iterations,
    classified_error)`` so the surrounding tools node can promote
    allowlisted ``state_updates`` keys (Stream K.K8) into the
    ``AgentState`` update dict, accumulate ``refund_iterations`` (Stream
    L.L5), and route ``classified_error`` into the CM-1
    ``<recovery-advisory>`` channel. ``state_updates`` is empty and
    refund is ``0`` for every code path that does not produce a
    successful :class:`~orchestrator.tools.registry.ToolResult` (errors,
    blocks, unknown tools); ``classified_error`` is ``None`` on the
    success path and set on every failure path.

    Stream TE-2 — each dispatch emits one ``TOOL_CALL`` audit row
    (``result=ERROR`` when the tool returns an error) or, when a
    pre-dispatch middleware blocks the call, one ``TOOL_BLOCKED`` row.
    The emit is best-effort: a missing ``audit_logger`` / ``tenant_id``
    is skipped and an audit-write failure is swallowed, so auditing never
    changes the dispatch result (mirrors ``sse._emit_run_end_audit``).
    """
    name = str(tool_call.get("name", ""))
    call_id = str(tool_call.get("id", ""))
    args = tool_call.get("args") or {}
    started = time.monotonic()

    try:
        if before_tool_dispatch_chain is not None:
            mw_ctx = MiddlewareContext(payload={"tool_name": name, "tool_args": dict(args)})
            await before_tool_dispatch_chain.invoke(mw_ctx, _noop)
            # Middlewares may rewrite tool_args (e.g., redact PII before
            # dispatch); tool_name is treated as immutable.
            args = mw_ctx.payload.get("tool_args", args) or {}

        tool = registry.get_required(name)
        # 10.1 — one ``helix.orchestrator.tool_call`` child span per tool
        # dispatch, attached under the session root span.
        with helix_span(HelixComponent.ORCHESTRATOR, "tool_call", attributes={"tool": name}):
            outcome = await _invoke_tool(
                tool,
                args,
                call_id,
                ctx,
                overflow_writer=overflow_writer,
                spotlight_nonce=spotlight_nonce,
            )
        ok = outcome[0].status != "error"
        # Stream HX-12 (Mini-ADR HX-I4) — call-through: the model called a
        # deferred name directly (it remembered the tool without a
        # find_tools round-trip). Dispatch already routes (TE-6 keeps
        # deferred tools in the lookup table); what was missing is the
        # promotion — without it the schema never enters the next turn's
        # bind and the model keeps calling blind. Piggyback the promote
        # on the tool's own state updates.
        if name in registry.deferred_names():
            message, state_updates, refund, classified = outcome
            merged_updates = dict(state_updates)
            promoted = merged_updates.get("promoted_tools")
            merged_updates["promoted_tools"] = [
                *(promoted if isinstance(promoted, list) else []),
                name,
            ]
            outcome = (message, merged_updates, refund, classified)
            promotion_events.labels(event="call_through").inc()
        _record_tool_metrics(name, started, "ok" if ok else "error")
        await _emit_tool_audit(
            audit_logger,
            ctx,
            name=name,
            call_id=call_id,
            args=args,
            path_args=tool.spec.path_args,
            from_skill=tool.spec.from_skill,
            action=AuditAction.TOOL_CALL,
            result=AuditResult.SUCCESS if ok else AuditResult.ERROR,
            reason=None if ok else "tool_error",
            duration_ms=_elapsed_ms(started),
            # Stream 14.4 — MCP traffic audit: server + response volume.
            extra_details=_mcp_audit_details(
                name, content=str(outcome[0].content), is_error=not ok
            ),
        )
        return outcome
    except ToolNotFoundError as exc:
        logger.warning("tools.unknown_tool name=%s call_id=%s", name, call_id)
        _record_tool_metrics(name, started, "error")
        await _emit_tool_audit(
            audit_logger,
            ctx,
            name=name,
            call_id=call_id,
            args=args,
            path_args=(),
            from_skill=None,
            action=AuditAction.TOOL_CALL,
            result=AuditResult.ERROR,
            reason="unknown_tool",
            duration_ms=_elapsed_ms(started),
            extra_details=_mcp_audit_details(name, is_error=True),
        )
        # Stream HX-12 — a truly unknown name gets ranked suggestions from
        # the deferred pool instead of a dead-end error (fail-open: worst
        # case is the unchanged bare error).
        content = _format_error(exc)
        try:
            suggestions = [spec.name for spec in registry.search(name)[:3]]
        except Exception:
            suggestions = []
        if suggestions:
            content += (
                f" Did you mean: {', '.join(suggestions)}? "
                "Use find_tools to search for and load tools."
            )
        return (
            ToolMessage(
                content=content,
                tool_call_id=call_id,
                status="error",
                name=name,
            ),
            {},
            0,
            classify_tool_error(tool_name=name, error=exc, spec=None),
        )
    except Exception as exc:
        # A pre-dispatch middleware (or the tool itself) may raise to block —
        # wrap so the LLM sees a normal error result rather than the run
        # crashing (Mini-ADR E-12).
        logger.warning(
            "tools.before_dispatch_blocked name=%s call_id=%s err=%s",
            name,
            call_id,
            type(exc).__name__,
        )
        _record_tool_metrics(name, started, "blocked")
        await _emit_tool_audit(
            audit_logger,
            ctx,
            name=name,
            call_id=call_id,
            args=args,
            path_args=(),
            from_skill=None,
            action=AuditAction.TOOL_BLOCKED,
            result=AuditResult.DENIED,
            reason=type(exc).__name__,
            duration_ms=_elapsed_ms(started),
            extra_details=_mcp_audit_details(name, is_error=True),
        )
        return (
            ToolMessage(
                content=_format_error(exc),
                tool_call_id=call_id,
                status="error",
                name=name,
            ),
            {},
            0,
            classify_tool_error(tool_name=name, error=exc, blocked=True),
        )


def _elapsed_ms(started: float) -> int:
    """Whole milliseconds elapsed since a ``time.monotonic`` timestamp."""
    return int((time.monotonic() - started) * 1000)


def _metric_tool_label(name: str) -> str:
    """Bound the ``tool`` metric label (Stream TE-3).

    MCP tool names are server-defined (``mcp:<server>.<tool>``) and thus
    not bounded by anything we author — one server can expose dozens of
    tools. Collapse them to ``mcp:<server>`` so the label stays bounded by
    the (catalog-curated, pool-capped) server set. The exact tool name is
    still recorded in the TE-2 audit row, the right home for unbounded
    identifiers. Builtin / manifest-authored HTTP / skill tool names are
    human-authored and finite-per-config, so they pass through unchanged.
    """
    if name.startswith("mcp:"):
        return name.split(".", 1)[0]
    return name


def _record_tool_metrics(name: str, started: float, outcome: str) -> None:
    """Emit per-tool Prometheus metrics for one dispatch (Stream TE-3).

    Unconditional (unlike the audit emit, which needs a tenant): every
    dispatch increments ``helix_tool_call_total{tool,outcome}`` and
    observes ``helix_tool_latency_seconds{tool}``. ``outcome`` is one of
    ``ok`` / ``error`` / ``blocked``; ``tool`` is normalised for cardinality
    via :func:`_metric_tool_label`.
    """
    label = _metric_tool_label(name)
    _tool_call_total.labels(tool=label, outcome=outcome).inc()
    _tool_latency_seconds.labels(tool=label).observe(time.monotonic() - started)


def _mcp_audit_details(
    name: str, *, content: str | None = None, is_error: bool = False
) -> dict[str, Any] | None:
    """Stream 14.4 — structured MCP traffic dimensions for the audit row.

    Returns ``None`` for non-MCP tools (the generic ``tool:call`` audit is
    unchanged). For an ``mcp:<server>.<tool>`` name it returns the server +
    bare tool as structured fields (so operators filter MCP traffic without
    parsing the name) plus, on the success path, ``response_chars`` — the size
    of the textified MCP response, a data-volume / exfil signal. The response
    CONTENT is never recorded (privacy / clear-text-logging), only its length.
    """
    if not name.startswith("mcp:"):
        return None
    server, _, tool = name[len("mcp:") :].partition(".")
    details: dict[str, Any] = {"mcp_server": server, "mcp_tool": tool, "mcp_is_error": is_error}
    if content is not None:
        details["response_chars"] = len(content)
    return details


#: Sandbox executors whose submitted code/command IS recorded into the audit
#: trail (a capped preview + a full-content sha256). This is the deliberate
#: "audit over blocking" trade for sandbox execution: the gVisor sandbox (read-
#: only rootfs, cap-drop, no-new-privileges, pids/mem/cpu caps, proxy-only egress)
#: is the real boundary, so we no longer denylist calls like ``subprocess.run``
#: (which a soffice/poppler skill legitimately needs) — instead every run is
#: traceable. See docs/design/sandbox-audit-evaluation.md.
_SANDBOX_CODE_ARGS: dict[str, tuple[str, ...]] = {
    "exec_python": ("code", "script"),
    "bash": ("command", "cmd"),
}
#: Cap the stored preview so an audit row stays bounded; the sha256 covers the
#: full content for forensic matching.
_CODE_PREVIEW_MAX = 4000


async def _emit_tool_audit(
    audit_logger: AuditLogger | None,
    ctx: ToolContext,
    *,
    name: str,
    call_id: str,
    args: Mapping[str, Any],
    path_args: tuple[str, ...],
    from_skill: str | None,
    action: AuditAction,
    result: AuditResult,
    reason: str | None,
    duration_ms: int,
    extra_details: Mapping[str, Any] | None = None,
) -> None:
    """Write one per-tool-call audit row (Stream TE-2).

    Best-effort and non-fatal: skipped when no ``audit_logger`` or no
    ``tenant_id`` (dev / unit-test path), and any write failure is logged
    and swallowed so auditing never breaks a tool dispatch.

    Privacy: ``details`` records the **argument names** and the declared
    path-arg **values** (filesystem paths) — never other raw argument values,
    which may carry PII / credentials (CodeQL clear-text-logging;
    [memory:feedback_codeql_clear_text_logging_secret_name]). The one
    exception is **sandbox executor code** (``exec_python`` / ``bash``): a
    capped preview + full-content sha256 are recorded as the traceability
    substitute for the removed call denylist (audit over blocking).
    """
    if audit_logger is None or ctx.tenant_id is None:
        return
    # The ENTIRE body — including the ``details`` build (``str(...)`` on
    # arbitrary arg keys / declared path values can in principle raise) —
    # is wrapped so this helper is genuinely total. On the success path the
    # call site sits inside ``_dispatch_tool``'s try whose ``except`` is the
    # middleware-block handler; an exception escaping here would otherwise
    # misclassify a successful dispatch as TOOL_BLOCKED (review HIGH).
    try:
        details: dict[str, Any] = {
            "tool": name,
            "call_id": call_id,
            "arg_keys": sorted(str(k) for k in args),
            "duration_ms": duration_ms,
        }
        if path_args:
            details["paths"] = [str(args[a]) for a in path_args if a in args]
        code_keys = _SANDBOX_CODE_ARGS.get(name)
        if code_keys is not None:
            for key in code_keys:
                value = args.get(key)
                if isinstance(value, str):
                    raw = value.encode("utf-8", "replace")
                    details["code_sha256"] = hashlib.sha256(raw).hexdigest()
                    details["code_bytes"] = len(raw)
                    details["code"] = (
                        value
                        if len(value) <= _CODE_PREVIEW_MAX
                        else value[:_CODE_PREVIEW_MAX] + "…(truncated)"
                    )
                    break
        if from_skill is not None:
            details["from_skill"] = from_skill
        if ctx.run_id is not None:
            details["run_id"] = str(ctx.run_id)
        # Stream 14.4 — MCP traffic dimensions (server / response volume), merged
        # last so the structured fields sit alongside the generic tool details.
        if extra_details:
            details.update(extra_details)
        await audit_logger.write(
            AuditEntry(
                tenant_id=ctx.tenant_id,
                actor_type="agent",
                actor_id=str(ctx.run_id) if ctx.run_id is not None else "agent",
                action=action,
                resource_type="tool",
                resource_id=name,
                result=result,
                reason=reason,
                details=details,
            )
        )
    except Exception:
        logger.exception("tools.audit_failed name=%s call_id=%s", name, call_id)


async def _emit_output_guard_audit(
    audit_logger: AuditLogger | None,
    tenant_id: object,
    *,
    action: AuditAction,
    result: AuditResult,
    categories: tuple[str, ...],
) -> None:
    """Durable audit row for an output-guard event (audit-eval Phase 4).

    PI-2 output screen blocks + 7.4 DLP redactions were previously metric-only;
    this records a per-event row (only the fired *categories*, never the matched
    value). Best-effort: no logger / no tenant / write failure never breaks the
    run. ``tenant_id`` is accepted loosely (str | UUID) and coerced.
    """
    if audit_logger is None or tenant_id is None:
        return
    try:
        tid = tenant_id if isinstance(tenant_id, UUID) else UUID(str(tenant_id))
    except (TypeError, ValueError):
        return
    try:
        await audit_logger.write(
            AuditEntry(
                tenant_id=tid,
                actor_type="agent",
                actor_id="agent",
                action=action,
                resource_type="run",
                resource_id="agent",
                result=result,
                details={"categories": list(categories)},
            )
        )
    except Exception:
        logger.exception("output_guard.audit_failed action=%s", action)


async def _emit_state_projected_audit(
    audit_logger: AuditLogger | None, ctx: ToolContext, *, written: tuple[str, ...]
) -> None:
    """Audit one ``DB→/workspace`` projection (Stream CM-0). Best-effort —
    a failed audit must not break the run. ``resource_type`` reuses the
    existing ``user_workspace`` (Mini-ADR CM-A6)."""
    if audit_logger is None or ctx.tenant_id is None:
        return
    try:
        details: dict[str, Any] = {"written": list(written)}
        if ctx.run_id is not None:
            details["run_id"] = str(ctx.run_id)
        await audit_logger.write(
            AuditEntry(
                tenant_id=ctx.tenant_id,
                actor_type="agent",
                actor_id=str(ctx.run_id) if ctx.run_id is not None else "agent",
                action=AuditAction.STATE_PROJECTED,
                resource_type="user_workspace",
                result=AuditResult.SUCCESS,
                details=details,
            )
        )
    except Exception:
        logger.exception("workspace_projection.audit_failed")


async def _project_workspace_state(
    factory: Callable[[ToolContext], WorkspaceFileWriter] | None,
    state: AgentState,
    ctx: ToolContext,
    audit_logger: AuditLogger | None,
) -> ProjectionResult | None:
    """Best-effort turn-end ``DB→/workspace`` projection (Stream CM-0).

    Renders ``AgentState.plan`` + recalled memories into PLAN.md / TODO.md /
    MEMORY.md and writes them through a per-turn :class:`WorkspaceFileWriter`
    (built from ``factory``), skipping when content is unchanged since
    ``last_projection_hash``. Never raises — projection must not break a run
    (Mini-ADR CM-A8) — returning ``None`` when disabled or on error."""
    if factory is None:
        return None
    try:
        result = await WorkspaceProjector(writer=factory(ctx)).project(
            plan=state.get("plan"),
            memories=state.get("recalled_memories") or [],
            last_digest=state.get("last_projection_hash"),
        )
    except Exception:
        logger.exception("workspace_projection.turn_failed")
        _cm_projection_total.labels(outcome="error").inc()
        return None
    if result.skipped:
        _cm_projection_total.labels(outcome="skipped").inc()
    elif result.written:
        _cm_projection_total.labels(outcome="projected").inc()
        await _emit_state_projected_audit(audit_logger, ctx, written=result.written)
    return result


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
    # Mini-ADR J-40 — global deadline lands in config["configurable"]
    # ["deadline_at"] (a ``time.monotonic`` timestamp). ``None`` when the
    # manifest carries no ``policies.run_deadline_s``.
    deadline_raw = configurable.get("deadline_at")
    deadline_at = float(deadline_raw) if isinstance(deadline_raw, int | float) else None
    # 1.3 Orchestrator-Worker — the per-run spawn budget is created once in
    # ``sse.run_agent`` and lives in config["configurable"]; ``None`` when the
    # feature is unwired. Read verbatim (mirrors cancellation_token).
    worker_spawn_budget = configurable.get("worker_spawn_budget")
    # MCP-OAUTH (OA-3b-后续) — the caller's OAuth subject id (a string), kept
    # distinct from user_id so a child run can resolve the same per-user OAuth
    # pool. ``None`` when absent (no OAuth identity).
    oauth_raw = configurable.get("oauth_user_id")
    oauth_user_id = oauth_raw if isinstance(oauth_raw, str) and oauth_raw else None
    return ToolContext(
        tenant_id=tenant_id,
        run_id=run_id,
        user_id=user_id,
        oauth_user_id=oauth_user_id,
        cancellation_token=cancellation_token(config),
        plan=plan,
        deadline_at=deadline_at,
        worker_spawn_budget=worker_spawn_budget,
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


def _validate_tool_args(tool: Tool, args: Mapping[str, Any]) -> str | None:
    """2.2 — validate ``args`` against the tool's JSON Schema before dispatch.

    Returns ``None`` when valid (or when the tool declares no schema), else a
    concise, value-free message naming the offending paths + failed keyword.
    The args are LLM-generated and may be malformed (model slip or injection);
    catching them here gives the model a grounded fix-it signal instead of an
    opaque downstream crash. A malformed *schema* (the tool's own bug) is not
    allowed to block dispatch — we skip validation in that case.
    """
    schema = tool.spec.parameters
    if not schema:
        return None
    validator_cls = Draft202012Validator
    try:
        validator_cls.check_schema(schema)
    except SchemaError:
        return None
    errors = sorted(validator_cls(schema).iter_errors(args), key=lambda e: list(e.absolute_path))
    if not errors:
        return None
    # Name path + failed keyword only — never echo the offending value.
    parts = [f"{e.json_path} ({e.validator})" for e in errors[:5]]
    return "arguments failed schema validation: " + "; ".join(parts)


async def _invoke_tool(
    tool: Tool,
    args: dict[str, Any],
    call_id: str,
    ctx: ToolContext,
    *,
    overflow_writer: WorkspaceFileWriter | None = None,
    spotlight_nonce: str | None = None,
) -> tuple[ToolMessage, Mapping[str, Any], int, ClassifiedToolError | None]:
    schema_error = _validate_tool_args(tool, args)
    if schema_error is not None:
        return (
            ToolMessage(
                content=f"[invalid args] {schema_error}",
                tool_call_id=call_id,
                status="error",
                name=tool.spec.name,
            ),
            {},
            0,
            classified_invalid_arguments(tool_name=tool.spec.name, summary=schema_error),
        )
    try:
        result = await tool.call(args, ctx=ctx)
    except Exception as exc:
        logger.warning(
            "tools.dispatch_failed name=%s call_id=%s err=%s",
            tool.spec.name,
            call_id,
            type(exc).__name__,
        )
        # CM-1 / CM-B3 — classify here, where the real exception (and the
        # tool's capability spec) are in hand, rather than re-parsing the
        # formatted ToolMessage downstream.
        classified = classify_tool_error(tool_name=tool.spec.name, error=exc, spec=tool.spec)
        return (
            ToolMessage(
                content=_format_error(exc),
                tool_call_id=call_id,
                status="error",
                name=tool.spec.name,
            ),
            {},
            0,
            classified,
        )
    # Stream CM-5 — recoverable compression: save an oversized result to the
    # workspace and let the LLM see a recoverable reference (the tool's own
    # truncated body, or a head+tail preview for tools that didn't pre-truncate)
    # instead of a dead end or a context blowup.
    replacement_body, footer = await _externalize_tool_overflow(
        result, tool, call_id, ctx, overflow_writer
    )
    body = replacement_body if replacement_body is not None else result.content
    # Stream PI-1b — a tool's output is untrusted (web pages, MCP servers, files
    # an attacker can control = the classic indirect-injection vector). Spotlight
    # it so embedded instructions read as data. The helix-owned overflow footer
    # stays trusted (outside the fence).
    tool_content = spotlight_untrusted(body, nonce=spotlight_nonce) if spotlight_nonce else body
    content = tool_content + footer if footer is not None else tool_content
    # ``artifact`` surfaces the tool's structured metadata (``ToolResult.meta``
    # — e.g. ask_image's ``image_ref`` / VL usage, truncation flags) in the raw
    # event stream / audit / trace. It rides alongside ``content`` but is NOT
    # sent back to the LLM, so it never affects the model's input.
    artifact = dict(result.meta) if result.meta else None
    return (
        # ``name`` records which tool produced this result (for MCP tools,
        # ``mcp:server.tool``). LangChain leaves it null unless set, so the raw
        # ToolMessage, audit, and trace all lose the attribution otherwise.
        ToolMessage(content=content, tool_call_id=call_id, name=tool.spec.name, artifact=artifact),
        result.state_updates,
        result.refund_iterations,
        None,
    )


async def _externalize_tool_overflow(
    result: ToolResult,
    tool: Tool,
    call_id: str,
    ctx: ToolContext,
    writer: WorkspaceFileWriter | None,
) -> tuple[str | None, str | None]:
    """Externalize an oversized tool result to the workspace (Stream CM-5).

    Returns ``(replacement_body, footer)``:

    * ``replacement_body`` — the in-context content to use INSTEAD of
      ``result.content`` (a head+tail preview), or ``None`` to keep
      ``result.content`` unchanged.
    * ``footer`` — the reference footer to append, or ``None``.

    Two trigger paths:

    1. **``full_content`` set** (bash / exec_python / http / mcp) — the tool
       already truncated ``content``; save the full rendering, keep the
       tool's truncated body, append the reference.
    2. **Generalized size budget** (tool-result-context-budget) — the tool did
       NOT set ``full_content`` but its ``content`` exceeds
       ``EXTERNALIZE_MIN_CHARS`` (e.g. ``web_search``). Save the content,
       replace the body with a head+tail preview, append the reference. This is
       what keeps many medium results (8x web_search) from accumulating into a
       context blowup.

    Best-effort (Mini-ADR CM-F5): a write failure never affects the run — the
    ``full_content`` path keeps the already-truncated body; the generalized path
    degrades to in-place head+tail truncation so context is still bounded.
    The reference footer is returned only after the write lands (it must never
    point at a file that does not exist). The fetch-back readers
    (:data:`EXEMPT_TOOLS`) are skipped — their source is cheaply re-readable, so
    externalizing them would just create a persist→read→persist loop (CM-F3).
    """
    if writer is None or tool.spec.name in EXEMPT_TOOLS:
        return None, None

    if result.full_content is not None:
        source = result.full_content
        replacement: str | None = None  # keep the tool's already-truncated body
    elif len(result.content) > EXTERNALIZE_MIN_CHARS:
        source = result.content
        replacement = make_preview(result.content)
    else:
        return None, None

    rel = overflow_rel_path(run_id=ctx.run_id, call_id=call_id, tool_name=tool.spec.name)
    try:
        await writer.write(rel=rel, content=clamp_overflow(source))
    except (asyncio.CancelledError, RunCancelledError):
        raise
    except Exception as exc:
        logger.warning(
            "tool.overflow_failed tool=%s rel=%s err=%s",
            tool.spec.name,
            rel,
            type(exc).__name__,
        )
        _cm_tool_overflow_total.labels(outcome="degraded", tool=tool.spec.name).inc()
        # Generalized path: bound context in-place (no file to reference);
        # full_content path: keep the tool's own truncated body as before.
        if replacement is not None:
            return fallback_truncate(result.content), None
        return None, None

    total_chars = len(source)
    _cm_tool_overflow_total.labels(outcome="externalized", tool=tool.spec.name).inc()
    _cm_tool_overflow_chars.set(total_chars)
    logger.info("tool.overflow tool=%s rel=%s chars=%d", tool.spec.name, rel, total_chars)
    return replacement, render_overflow_footer(rel=rel, total_chars=total_chars)


def _format_error(exc: BaseException) -> str:
    summary = str(exc)
    if len(summary) > _ERROR_SUMMARY_MAX_CHARS:
        summary = summary[:_ERROR_SUMMARY_MAX_CHARS] + "...[truncated]"
    return f"[tool error] {type(exc).__name__}: {summary}"
