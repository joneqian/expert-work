"""Canonical LangGraph state shape for orchestrator graphs.

Per [STREAM-E-DESIGN § 2.3](../../../../docs/streams/STREAM-E-DESIGN.md),
fields are added incrementally across the Stream E sub-PRs:

- **E.1**: ``messages`` (LangGraph reducer-style append)
- **E.6**: ``step_count`` + ``max_steps`` for the ReAct loop guard

Every ``AgentState`` channel is checkpointed (dill), so **non-serialisable
runtime objects do not live here**. They travel via the
``config["configurable"]`` channel instead — it is per-invocation and not
checkpointed:

- Tenant binding (``tenant_id`` / ``session_id`` / ``run_id``) — LangGraph idiom.
- ``cancellation_token`` (E.15) — backed by a live ``asyncio.Event``.
- The ``LLMRouter`` holds its own provider chain + fallback state (E.11).
"""

from __future__ import annotations

from operator import add
from typing import Annotated, Any, NotRequired, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from helix_agent.protocol import (
    ApprovalRequest,
    MemoryItem,
    Plan,
    Reflection,
    SubAgentInvocation,
)

# CM-1 — the channel's element type lives in the ``tools`` layer (same
# layer as the L-4 ``mutation_classifier`` this generalises), so a normal
# runtime import is cycle-free and lets ``get_type_hints(AgentState)``
# resolve the annotation when LangGraph introspects the state schema.
from orchestrator.tools.error_classifier import ClassifiedToolError

#: Default ReAct hard limit — see Mini-ADR E-6 in the design doc + the
#: "ReAct 无限循环" risk row. Manifest may override per-agent.
DEFAULT_MAX_STEPS = 20


def _merge_promoted(existing: list[str] | None, new: list[str] | dict[str, list[str]]) -> list[str]:
    """Reducer for :attr:`AgentState.promoted_tools` — Stream TE-6 / HX-12.

    ``find_tools`` writes the names of deferred tools it just retrieved; this
    reducer unions them into the run's accumulated set, deduplicating while
    keeping a stable order (``existing`` first, then names from ``new`` not
    already present). Accumulating across turns means a tool stays promoted
    once retrieved. The state lives on the LangGraph channel — per-thread,
    checkpointed — so promotion never leaks into the cached registry.

    Stream HX-12 (Mini-ADR HX-I5) adds a removal shape for the demotion
    path: ``new`` may be ``{"add": [...], "remove": [...]}``. The plain
    ``list`` shape keeps the original add-only semantics so every existing
    write site is untouched; removal never deletes the tool from the
    registry's deferred pool — a demoted tool is re-promotable any time.
    """
    if isinstance(new, dict):
        to_add = list(new.get("add", []))
        to_remove = set(new.get("remove", []))
    else:
        to_add = list(new)
        to_remove = set()
    out: list[str] = [name for name in (existing or []) if name not in to_remove]
    seen = set(out)
    for name in to_add:
        if name not in seen and name not in to_remove:
            out.append(name)
            seen.add(name)
    return out


def _merge_last_used(existing: dict[str, int] | None, new: dict[str, int]) -> dict[str, int]:
    """Reducer for :attr:`AgentState.promoted_tool_last_used` — Stream HX-12.

    Per-key max merge: ``tools_node`` stamps the current ``step_count`` for
    every promoted tool that dispatched (and for names freshly promoted in
    the batch, so each entry has a baseline). The demotion gate compares
    these stamps against the current step to find stale promotions.
    """
    out = dict(existing or {})
    for name, step in new.items():
        if step > out.get(name, -1):
            out[name] = step
    return out


class AgentState(TypedDict):
    """State threaded through every orchestrator LangGraph node.

    ``messages`` uses LangGraph's ``add_messages`` reducer so nodes
    returning ``{"messages": [...]}`` append to (rather than overwrite)
    the conversation history. ``step_count`` and ``max_steps`` use the
    default overwrite reducer — the agent node sets the new count each
    turn, and ``max_steps`` is configured once at graph construction.

    ``plan`` (Stream J.1) is set once by the ``planner`` node when the
    manifest's ``workflow.type`` is ``plan_execute``; it is absent for
    plain ``react`` graphs. ``NotRequired`` so the ReAct input shape is
    unchanged — readers use ``state.get("plan")``.

    ``reflections`` (Stream J.2) accumulates one :class:`Reflection` per
    ``reflect`` node entry — an ``operator.add`` reducer appends. Absent
    unless the manifest carries a ``reflection:`` block.

    ``recalled_memories`` (Stream J.3) is set once by the ``memory_recall``
    node — the long-term memories ``agent_node`` renders into its system
    context. Absent unless the manifest enables long-term memory.

    ``step_count_refund_pending`` (Stream L.L5 / Mini-ADR L-5) is the
    narrow channel a ``tools_node`` writes when one or more tools
    returned :attr:`~orchestrator.tools.registry.ToolResult.refund_iterations`
    greater than zero. The next ``agent_node`` subtracts it from
    ``step_count`` (clamped at 0) before computing the post-turn count,
    then resets the channel to ``0``. Keeps refund accounting
    observable and auditable instead of letting tools rewrite
    ``step_count`` directly.

    ``tool_failures`` (Stream CM-1, generalising L.L4) accumulates
    :class:`~orchestrator.tools.error_classifier.ClassifiedToolError`
    rows for tool calls that failed in the most recent ``tools`` batch —
    both error-path failures (classified at the catch site from the real
    exception) and the success-path ``mutation_not_landed`` case folded
    in from L-4's mutation classifier. The next ``agent_node`` reads the
    list, emits a ``<recovery-advisory>`` ``HumanMessage`` with grounded
    per-tool recovery guidance, and resets the channel to ``[]``.
    Defaults to empty; tools_node only writes when at least one tool
    failed.

    ``subagent_invocations`` (Stream J.4-补强-2 / Mini-ADR J-40)
    accumulates one
    :class:`~helix_agent.protocol.subagent.SubAgentInvocation` per
    SubAgentTool delegation — every outcome path (success / max_steps /
    cancelled / future timed_out) appends a terminal-state row via the
    ``operator.add`` reducer. Lets the parent's LangGraph checkpoint
    carry the full delegation history (audit + J.13 eval replay), and
    feeds future M2-B fan-in aggregation (iteration_used sum /
    llm_call_count sum / wall_clock_ms max). Absent unless the manifest
    declares ``subagents``.

    ``pending_approval`` (Stream J.8 / Mini-ADR J-24) carries the
    :class:`~helix_agent.protocol.approval.ApprovalRequest` a run is
    paused on — ``tools_node`` writes it before the run routes to END
    (RunStatus.PAUSED). The overwrite reducer applies: a resume clears
    it back to ``None``. Absent on a run that has never paused.

    ``approval_resume`` (Stream J.8-step3b) is the transient channel the
    resume endpoint writes via ``aupdate_state`` — a
    ``{"decision", "modified_args"}`` dict. ``tools_node`` reads it on
    re-entry to apply the human verdict (approve dispatches the gated
    tool_call, modify rewrites its args, reject synthesises a rejection
    ``ToolMessage``) and clears it back to ``None``.

    ``approval_outcome`` (Stream J.8-step3b) is the terminal signal a
    declarative-gate *reject* sets — ``_after_tools`` routes the run to
    END when it is ``"rejected"`` (the platform vetoed the run). An
    agent-initiated ``ask_for_approval`` reject leaves it unset so the
    run loops back to the agent.

    ``promoted_tools`` (Stream TE-6) carries the names of deferred tools
    the ``find_tools`` meta-tool has retrieved this run. ``find_tools``
    writes via :attr:`ToolResult.state_updates`; the ``_merge_promoted``
    reducer union-dedupes across turns. The next ``agent_node`` adds the
    matching deferred specs to the LLM bind so the promoted tools become
    callable. Per-thread + checkpointed, so promotion stays isolated to
    the run and never mutates the cached registry. Absent (treated as ``[]``)
    until ``find_tools`` first promotes — zero behaviour change when no
    tool is deferred.

    ``last_projection_hash`` (Stream CM-0 / Mini-ADR CM-A3) is the content
    digest of the most recent ``DB → /workspace`` projection (PLAN.md /
    TODO.md / MEMORY.md). ``tools_node`` passes it to the
    :class:`~orchestrator.context.WorkspaceProjector` as the only-if-changed
    baseline and writes the new digest back, so an unchanged turn skips the
    sandbox round-trip. Absent until the first projection; ``None`` means
    nothing has been projected yet.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    step_count: int
    max_steps: int
    plan: NotRequired[Plan | None]
    reflections: NotRequired[Annotated[list[Reflection], add]]
    recalled_memories: NotRequired[list[MemoryItem]]
    step_count_refund_pending: NotRequired[int]
    #: Stream CM-9 (Mini-ADR CM-J5) — transient escalation signal. Set by
    #: agent_node when the loop-detection middleware flags a repeat
    #: (``ctx.payload["loop_detected"]``); the NEXT agent step consumes it
    #: (one turn on the escalated, higher-effort caller) and resets it.
    escalate_next: NotRequired[bool]
    #: Stream CM-11 — the plan goal as of the previous agent turn. agent_node
    #: sets it each turn to the current ``plan.goal`` (``None`` for react
    #: graphs). A change versus the live plan goal — a re-plan, or a human
    #: PLAN.md edit ingested via CM-0 — fires one escalated "re-calibrate"
    #: turn (Mini-ADR CM-M1). Absent on the first plan turn, so the initial
    #: decomposition (already deep-thought by the planner) never re-fires.
    last_plan_goal: NotRequired[str | None]
    tool_failures: NotRequired[list[ClassifiedToolError]]
    subagent_invocations: NotRequired[Annotated[list[SubAgentInvocation], add]]
    pending_approval: NotRequired[ApprovalRequest | None]
    approval_resume: NotRequired[dict[str, Any] | None]
    approval_outcome: NotRequired[str | None]
    promoted_tools: NotRequired[Annotated[list[str], _merge_promoted]]
    #: Stream HX-12 — step_count stamp of each promoted tool's last dispatch
    #: (baseline = the step it was promoted). Feeds the demotion gate: a
    #: promoted tool unused for N turns is dropped from ``promoted_tools``
    #: when the compressor fires (it stays in the deferred pool — only the
    #: per-turn bind slims down).
    promoted_tool_last_used: NotRequired[Annotated[dict[str, int], _merge_last_used]]
    last_projection_hash: NotRequired[str | None]
