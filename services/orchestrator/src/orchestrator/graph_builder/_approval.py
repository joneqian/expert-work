"""Approval-gate helpers for ``tools_node`` — Stream J.8 (Mini-ADR J-24).

helix's ``tools_node`` dispatches a turn's ``tool_calls`` in parallel
stages (Stream L.L6 — ``plan_stages`` + ``asyncio.gather``). LangGraph's
native ``interrupt()`` re-runs the whole node on resume, which does not
compose cleanly with an in-flight ``gather``. After comparing with
deer-flow (whose ``ClarificationMiddleware`` returns ``Command(goto=END)``
on a serial tool loop), J.8 adopts the **end-and-resume** model:

* ``tools_node`` checks the turn's ``tool_calls`` *before* staging. If a
  call is approval-gated — either its name is in
  ``policies.approval_required_tools`` (the platform-enforced declarative
  gate) or it is the agent-initiated ``ask_for_approval`` builtin — the
  node writes an :class:`ApprovalRequest` to ``AgentState.pending_approval``
  and dispatches nothing. The graph then routes to ``END`` and the run
  ends as ``RunStatus.PAUSED`` with its checkpoint intact.
* ``POST /v1/runs/{id}/resume`` (J.8-step3) writes the human verdict
  back into the checkpoint and re-invokes the graph.

This module owns the *detection* + *request construction* — pure
functions, no graph imports, easily unit-tested.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.messages import ToolMessage

from helix_agent.protocol import ApprovalReasonKind, ApprovalRequest
from orchestrator.tools.approval import ASK_FOR_APPROVAL_TOOL

__all__ = [
    "ApprovalTarget",
    "ResumeOutcome",
    "apply_resume_decision",
    "build_approval_request",
    "find_approval_target",
]

#: ``reason_kind`` values an ``ask_for_approval`` call may carry. A call
#: with anything else (or nothing) falls back to ``risk_confirmation``.
_AGENT_REASON_KINDS: frozenset[str] = frozenset(
    {
        "missing_info",
        "ambiguous_requirement",
        "approach_choice",
        "risk_confirmation",
    }
)


class ApprovalTarget:
    """The first approval-gated ``tool_call`` found in a turn.

    ``index`` is the call's position in the turn's ``tool_calls`` list;
    ``is_agent_initiated`` distinguishes an ``ask_for_approval`` call
    (the agent asked) from a declarative-gate hit (the platform
    intervened) — they differ only in resume *reject* semantics
    (STREAM-J-DESIGN § 14.5).
    """

    __slots__ = ("index", "is_agent_initiated", "tool_call")

    def __init__(
        self,
        *,
        index: int,
        tool_call: Mapping[str, Any],
        is_agent_initiated: bool,
    ) -> None:
        self.index = index
        self.tool_call = tool_call
        self.is_agent_initiated = is_agent_initiated


def find_approval_target(
    tool_calls: list[dict[str, Any]],
    approval_required_tools: frozenset[str],
) -> ApprovalTarget | None:
    """Return the first approval-gated call in ``tool_calls``, or ``None``.

    A call is gated when it is the ``ask_for_approval`` builtin, or its
    name is in ``approval_required_tools``. M0 pauses on the *first*
    such call — the rest of the turn's calls are simply not dispatched
    this round; a resume re-runs the agent which re-decides.
    """
    for index, call in enumerate(tool_calls):
        name = call.get("name")
        if name == ASK_FOR_APPROVAL_TOOL:
            return ApprovalTarget(index=index, tool_call=call, is_agent_initiated=True)
        if name in approval_required_tools:
            return ApprovalTarget(index=index, tool_call=call, is_agent_initiated=False)
    return None


def _stable_request_id(thread_id: str, node: str, action_summary: str) -> str:
    """Deterministic id for an :class:`ApprovalRequest`.

    A retried turn (same thread, same node, same action) produces the
    same id, so a UI keying approvals by ``request_id`` never shows a
    duplicate — the same防重试 trick deer-flow uses for its
    clarification message ids.
    """
    digest = hashlib.sha256(f"{thread_id}\x00{node}\x00{action_summary}".encode()).hexdigest()
    return f"approval:{digest[:16]}"


def _coerce_reason_kind(raw: object) -> ApprovalReasonKind:
    """Map an ``ask_for_approval`` call's ``reason_kind`` arg to the enum.

    An unknown / missing value is not a hard error — the agent's
    free-form arg should never crash the run. Fall back to the most
    conservative kind (``risk_confirmation``).
    """
    if isinstance(raw, str) and raw in _AGENT_REASON_KINDS:
        return raw  # type: ignore[return-value]  # membership-checked
    return "risk_confirmation"


def build_approval_request(
    target: ApprovalTarget,
    *,
    thread_id: str,
    timeout_s: int,
    now: datetime | None = None,
) -> ApprovalRequest:
    """Construct the :class:`ApprovalRequest` for a gated ``tool_call``.

    Declarative-gate hits get ``reason_kind="policy_gate"`` and an
    auto-built summary. ``ask_for_approval`` calls carry the agent's own
    ``reason_kind`` / ``action_summary`` / ``proposed_args``.
    """
    moment = now or datetime.now(UTC)
    call = target.tool_call
    args = call.get("args") or {}
    if target.is_agent_initiated:
        reason_kind: ApprovalReasonKind = _coerce_reason_kind(args.get("reason_kind"))
        action_summary = str(args.get("action_summary") or "agent requested human approval")
        proposed_args = dict(args.get("proposed_args") or {})
    else:
        reason_kind = "policy_gate"
        tool_name = str(call.get("name") or "tool")
        action_summary = f"approval-gated tool '{tool_name}'"
        proposed_args = dict(args)
    return ApprovalRequest(
        request_id=_stable_request_id(thread_id, "tools", action_summary),
        node="tools",
        reason_kind=reason_kind,
        action_summary=action_summary,
        proposed_args=proposed_args,
        requested_at=moment,
        timeout_at=moment + timedelta(seconds=timeout_s),
    )


class ResumeOutcome:
    """The result of applying a human verdict to a paused turn's tool_calls.

    Exactly one of two shapes:

    * **dispatch** — ``reject_messages`` empty, ``tool_calls`` carries
      the (possibly arg-rewritten) calls to run normally.
    * **reject** — ``reject_messages`` carries one synthetic
      ``ToolMessage`` per call (so no orphan tool_call is left), and
      ``tool_calls`` is empty (nothing runs). ``terminal`` is ``True``
      for a declarative-gate reject (the platform vetoed the run →
      route to END) and ``False`` for an ``ask_for_approval`` reject
      (the agent just loops back, sees the rejection, re-plans).
    """

    __slots__ = ("reject_messages", "terminal", "tool_calls")

    def __init__(
        self,
        *,
        tool_calls: list[dict[str, Any]],
        reject_messages: list[ToolMessage],
        terminal: bool,
    ) -> None:
        self.tool_calls = tool_calls
        self.reject_messages = reject_messages
        self.terminal = terminal


def apply_resume_decision(
    tool_calls: list[dict[str, Any]],
    approval_required_tools: frozenset[str],
    resume: Mapping[str, Any],
) -> ResumeOutcome:
    """Apply a resume ``{decision, modified_args}`` to a paused turn.

    ``approve`` → dispatch every call unchanged. ``modify`` → rewrite
    the gated call's args with ``modified_args``, then dispatch.
    ``reject`` → dispatch nothing; return a rejection ``ToolMessage``
    per call. ``terminal`` is set for a declarative-gate reject.
    """
    decision = str(resume.get("decision", "approve"))
    target = find_approval_target(tool_calls, approval_required_tools)
    if decision == "reject":
        reason = str(resume.get("reason") or "approval rejected by reviewer")
        messages = [
            ToolMessage(
                content=f"[approval rejected] {reason}",
                tool_call_id=str(call.get("id") or ""),
                status="error",
            )
            for call in tool_calls
        ]
        # A declarative-gate reject vetoes the whole run; an
        # agent-initiated ask_for_approval reject just informs the agent.
        terminal = target is not None and not target.is_agent_initiated
        return ResumeOutcome(tool_calls=[], reject_messages=messages, terminal=terminal)
    if decision == "modify" and target is not None:
        modified = dict(resume.get("modified_args") or {})
        rewritten = [dict(call) for call in tool_calls]
        rewritten[target.index] = {**rewritten[target.index], "args": modified}
        return ResumeOutcome(tool_calls=rewritten, reject_messages=[], terminal=False)
    # approve (or modify with no target — defensive: dispatch unchanged).
    return ResumeOutcome(
        tool_calls=[dict(call) for call in tool_calls],
        reject_messages=[],
        terminal=False,
    )
