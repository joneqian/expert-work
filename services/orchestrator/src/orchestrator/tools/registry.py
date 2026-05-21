"""Tool Protocol + ``ToolRegistry`` — Stream E.6.

Concrete tool adapters (``web_search`` E.7, ``http`` E.8, ``mcp:*`` E.9,
``exec_python`` F.4) all implement :class:`Tool` and register here. The
ReAct graph (``orchestrator.graph_builder``) reads
:meth:`ToolRegistry.specs` to hand the LLM the list of callable tools,
and dispatches by name via :meth:`ToolRegistry.get`.

Tool ``call`` exceptions are wrapped into ``ToolMessage(error=...)`` by
the graph's ``tools`` node (per Mini-ADR E-12 in
[STREAM-E-DESIGN](../../../../../docs/streams/STREAM-E-DESIGN.md)) —
adapters can raise freely; the LLM sees the error as a tool result and
reasons about retry / different args / final answer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from helix_agent.protocol import Plan
from helix_agent.runtime.cancellation import CancellationToken


@dataclass(frozen=True)
class ToolSpec:
    """Static descriptor of a tool — handed to the LLM for tool selection.

    ``is_read_only`` and ``path_args`` (Stream L.L6) feed the ReAct
    ``tools`` node's adaptive parallel scheduler. Read-only tools
    without overlapping paths run concurrently; conflicting calls
    serialise. Defaults (``False`` / ``()``) are deliberately
    conservative — a third-party tool that doesn't opt in stays on the
    sequential path it had before L6. See [STREAM-L-DESIGN § 3.L6](
    ../../../../../docs/streams/STREAM-L-DESIGN.md) + Mini-ADR L-6.
    """

    name: str
    description: str
    #: JSON Schema for the tool's ``args`` parameter.
    parameters: Mapping[str, Any] = field(default_factory=dict)
    #: Stream L.L6 — when ``True``, multiple invocations of this tool
    #: (and concurrent invocations of other read-only tools) may run in
    #: parallel without conflict. Tools that mutate filesystem,
    #: ``AgentState``, sandbox, or any third-party state MUST keep the
    #: default ``False`` — they serialise against any tool that touches
    #: the same path (or against every other call, when no path is
    #: declared).
    is_read_only: bool = False
    #: Stream L.L6 — argument names whose values are filesystem-like
    #: paths the tool reads or writes. The scheduler detects conflicts
    #: between two tool calls by comparing the resolved values of
    #: these args. Empty tuple means "this tool has no per-call path";
    #: combined with ``is_read_only=False`` that yields the worst-case
    #: "conflicts with every other tool" stance (e.g., ``update_plan``
    #: writes ``AgentState.plan``, a global channel).
    path_args: tuple[str, ...] = ()
    #: Mini-ADR J-40 (J.4-补强-2) — a tool that **mutates** but whose
    #: invocations are nevertheless independent of one another. Multiple
    #: calls to such a tool (including against the same name) may share
    #: a stage and run via ``asyncio.gather``. ``SubAgentTool`` is the
    #: canonical example: each delegation spins up a fresh child
    #: ``thread_id`` / sandbox session, so two sub-agent calls don't
    #: collide. Defaults to ``False`` — third-party tools stay on the
    #: ``is_read_only`` path.
    is_parallel_safe: bool = False
    #: Stream J.7a (Mini-ADR J-23) — name of the skill that contributed
    #: this tool to the agent's registry, or ``None`` when the tool is
    #: declared directly in the manifest's ``tools:`` block. The dispatch
    #: path uses this to label the ``helix_skill_call_total`` /
    #: ``helix_skill_call_errors_total`` metrics so per-skill usage can
    #: be observed (Mini-ADR J-23 § 15.4 telemetry 双 counter).
    from_skill: str | None = None


@dataclass(frozen=True)
class ToolContext:
    """Per-invocation context threaded from the ReAct ``tools`` node.

    Most fields are optional because E.6 / E.7 tools didn't need any
    of them; E.8 HTTPTool is the first to require ``tenant_id`` (for
    the per-tenant allowlist lookup). Future tools read ``run_id`` for
    audit attribution. ``user_id`` (Stream J.15) scopes ``exec_python``'s
    persistent workspace volume — ``None`` when the run has no user
    binding. ``cancellation_token`` (Stream J.4) lets a tool propagate
    the run's cancellation into work it spawns — notably ``SubAgentTool``
    threading it into a child agent run.
    """

    tenant_id: UUID | None = None
    run_id: UUID | None = None
    user_id: UUID | None = None
    cancellation_token: CancellationToken | None = None
    #: Stream K.K8 — current plan (when ``workflow.type == "plan_execute"``).
    #: ``update_plan`` reads ``plan.goal`` so the agent's revised plan keeps
    #: the original goal (the tool only rewrites ``steps``). ``None`` for
    #: react-mode runs and any run before the planner node has executed.
    plan: Plan | None = None
    #: Mini-ADR J-40 (J.4-补强-2) — wall-clock deadline (``time.monotonic``
    #: timestamp) for the *current run including any sub-agent recursion*.
    #: Established once in ``sse.run_agent`` from the manifest's
    #: ``policies.run_deadline_s``; ``SubAgentTool`` propagates the value
    #: to child config unchanged (child does not reset). A tool that
    #: opts in checks ``deadline_at - time.monotonic() <= 0`` before
    #: doing expensive work and short-circuits with a cancel. ``None``
    #: when no deadline is configured.
    deadline_at: float | None = None


#: Stream K.K8 — keys a tool is allowed to write back to ``AgentState``
#: via :attr:`ToolResult.state_updates`. Limiting the set prevents a tool
#: from inadvertently rewriting unrelated channels (``messages``,
#: ``step_count`` …); add a key here when a new tool needs to mutate a
#: specific channel.
#:
#: Channels:
#: - ``plan`` — Stream J.1 / K.K8 ``update_plan``
#: - ``subagent_invocations`` — Stream J.4-补强-2 / Mini-ADR J-40
#:   ``SubAgentTool`` appends one :class:`SubAgentInvocation` per
#:   delegation outcome (the state.py channel uses ``operator.add``).
TOOL_ALLOWED_STATE_KEYS: frozenset[str] = frozenset({"plan", "subagent_invocations"})


@dataclass(frozen=True)
class ToolResult:
    """Result of a successful tool dispatch.

    ``content`` is fed back to the LLM as a ``ToolMessage`` body.
    ``meta`` carries truncation flags and any per-tool metadata (per
    Mini-ADR E-10 — caller knows e.g. ``meta.truncated=True`` ↔ output
    was cut).

    ``state_updates`` (Stream K.K8) is the narrow channel through which
    a tool may write back to :class:`AgentState`. The tools node
    promotes only keys in :data:`TOOL_ALLOWED_STATE_KEYS`; other keys
    are silently dropped (so a malformed or compromised tool can't
    rewrite ``messages`` or ``step_count``).

    ``refund_iterations`` (Stream L.L5 / Mini-ADR L-5) lets a tool ask
    the ReAct loop to refund iterations from the agent's ``step_count``
    budget. Internal-chain tools like ``update_plan`` (K.K8) shouldn't
    burn user-visible budget for housekeeping calls. The tools node
    accumulates this across the batch into
    ``step_count_refund_pending``; the next agent node subtracts it
    before computing the new ``step_count`` (clamped at 0 — refund
    never produces negative). Must be ``>= 0`` — a tool can't reverse
    the polarity and *consume* budget through this channel.
    """

    content: str
    meta: Mapping[str, Any] = field(default_factory=dict)
    state_updates: Mapping[str, Any] = field(default_factory=dict)
    refund_iterations: int = 0

    def __post_init__(self) -> None:
        # Frozen dataclass — direct setattr is disabled. The check runs
        # at construction time so a misbehaving tool fails loudly rather
        # than silently corrupting the agent's iteration budget.
        if self.refund_iterations < 0:
            msg = (
                f"ToolResult.refund_iterations must be >= 0 (got "
                f"{self.refund_iterations}); a tool cannot consume the "
                f"agent's iteration budget through this channel."
            )
            raise ValueError(msg)


@runtime_checkable
class Tool(Protocol):
    """Async callable wrapped with its static spec.

    ``spec`` is declared read-only so both a plain attribute (MCPTool's
    ``field(init=False)``) and a ``@property`` (WebSearchTool / HTTPTool)
    satisfy the Protocol.
    """

    @property
    def spec(self) -> ToolSpec:
        """The tool's static descriptor — handed to the LLM for selection."""

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        """Dispatch the tool with the given args and return a
        :class:`ToolResult`. ``ctx`` carries tenant binding etc. so
        per-tenant policies (E.8 allowlist, F.6 secret resolution) can
        run inside the tool. Implementations may raise; the ReAct graph's
        tools node wraps any exception into a ``ToolMessage(status='error')``
        (Mini-ADR E-12) — never let it propagate to the runner."""


class ToolNotFoundError(KeyError):
    """Raised by :meth:`ToolRegistry.get_required` when ``name`` isn't
    registered. The graph's ``tools`` node turns this into a
    ``ToolMessage(error=...)`` rather than propagating."""


class ToolBlockedError(RuntimeError):
    """Raised when a tool's policy denies the call (e.g. URL not in
    the per-tenant HTTP allowlist; tenant_id missing for a
    tenant-scoped tool). The graph's ``tools`` node wraps it into a
    ``ToolMessage(status='error')`` per Mini-ADR E-12 and the
    surrounding orchestrator writes a ``tool:blocked`` audit row."""


class ToolRegistry:
    """In-memory tool catalogue.

    M0 instantiates one per ``orchestrator`` process at startup;
    register all tools available to any agent. Per-agent / per-tenant
    filtering (``http_tool_allowlist`` / ``mcp_servers``) happens at
    dispatch / spec-resolution time — the registry itself is just a
    lookup table.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool by its spec ``name``. Re-registering replaces."""
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def get_required(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            msg = f"unknown tool: {name!r}"
            raise ToolNotFoundError(msg)
        return tool

    def specs(self) -> list[ToolSpec]:
        """Specs in registration order — handed to the LLM."""
        return [tool.spec for tool in self._tools.values()]

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
