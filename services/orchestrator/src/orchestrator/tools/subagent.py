"""Sub-agent delegation — Stream J.4 (agent-as-tool).

A manifest's ``spec.subagents`` block declares deployed agents the parent
may delegate to. The assembler (Stream J.4 PR4) wraps each entry into a
named ``SubAgentTool`` (PR3) so the parent's LLM sees delegation as an
ordinary tool call.

This module holds the scaffold the later PRs build on:

* :data:`MAX_SUBAGENT_DEPTH` — the hard recursion cap.
* :class:`ChildAgentBuilder` — the callback the control-plane injects so
  the orchestrator can resolve an ``agent_ref`` and build the referenced
  sub-agent (``AgentSpecStore`` lives in the control-plane; the
  orchestrator only ever holds pre-built ``BuiltAgent``\\s).

See [STREAM-J-DESIGN §11 / Mini-ADR J-12](../../../../../docs/streams/STREAM-J-DESIGN.md).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable
from uuid import UUID

if TYPE_CHECKING:
    from orchestrator.agent_factory import BuiltAgent

#: Hard cap on recursive sub-agent delegation depth. The top-level agent
#: builds at depth 0; each delegation step builds the child at parent
#: depth + 1. An agent built at this depth gets **no** ``SubAgentTool``
#: registered — structural recursion termination, so a cross-manifest
#: cycle (A→B→A) can never run away (Mini-ADR J-12). This replaces a
#: token-budget guard: helix has no runtime token budget, so cost is
#: bounded structurally by depth times each agent's ``max_iterations``.
MAX_SUBAGENT_DEPTH: Final = 3


@runtime_checkable
class ChildAgentBuilder(Protocol):
    """Resolves an ``agent_ref`` and builds the referenced sub-agent.

    Injected into :class:`~orchestrator.tools.ToolEnv` by the
    control-plane — the orchestrator cannot resolve an ``agent_ref``
    itself (the ``AgentSpecStore`` is control-plane-only). ``SubAgentTool``
    (Stream J.4 PR3) calls this inside ``call()`` to obtain the child
    ``BuiltAgent`` it delegates to.

    ``depth`` is the child's build-time recursion depth (parent depth
    + 1). The builder keys its agent cache on ``depth`` because the same
    manifest builds a *different* graph at different depths — an agent
    built at :data:`MAX_SUBAGENT_DEPTH` carries no further
    ``SubAgentTool``\\s.
    """

    async def __call__(
        self,
        *,
        tenant_id: UUID,
        name: str,
        version: str,
        depth: int,
    ) -> BuiltAgent:
        """Build the sub-agent ``name@version`` for ``tenant_id`` at ``depth``.

        Raises if the ``agent_ref`` does not resolve to a deployed,
        non-deleted AgentSpec — ``SubAgentTool`` turns that into a tool
        error rather than crashing the parent run.
        """
