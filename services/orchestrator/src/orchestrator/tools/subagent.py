"""Sub-agent delegation — Stream J.4 (agent-as-tool).

A manifest's ``spec.subagents`` block declares deployed agents the parent
may delegate to. The assembler (Stream J.4 PR4) wraps each entry into a
named :class:`SubAgentTool` so the parent's LLM sees delegation as an
ordinary tool call.

This module holds:

* :data:`MAX_SUBAGENT_DEPTH` — the hard recursion cap.
* :class:`ChildAgentBuilder` — the callback the control-plane injects so
  the orchestrator can resolve an ``agent_ref`` and build the referenced
  sub-agent (``AgentSpecStore`` lives in the control-plane; the
  orchestrator only ever holds pre-built ``BuiltAgent``\\s).
* :class:`SubAgentTool` — the :class:`~orchestrator.tools.registry.Tool`
  adapter: ``call()`` builds the child agent, runs it to completion on
  its own thread, and returns the child's final answer to the parent.

See [STREAM-J-DESIGN §11 / Mini-ADR J-12](../../../../../docs/streams/STREAM-J-DESIGN.md).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from helix_agent.protocol import SubAgentSpec, parse_agent_ref
from helix_agent.runtime.cancellation import CANCELLATION_TOKEN_KEY, CancellationToken
from orchestrator.errors import MaxStepsExceededError
from orchestrator.tools.registry import ToolBlockedError, ToolContext, ToolResult, ToolSpec

if TYPE_CHECKING:
    from orchestrator.agent_factory import BuiltAgent

logger = logging.getLogger(__name__)

#: Hard cap on recursive sub-agent delegation depth. The top-level agent
#: builds at depth 0; each delegation step builds the child at parent
#: depth + 1. An agent built at this depth gets **no** ``SubAgentTool``
#: registered — structural recursion termination, so a cross-manifest
#: cycle (A->B->A) can never run away (Mini-ADR J-12). This replaces a
#: token-budget guard: helix has no runtime token budget, so cost is
#: bounded structurally by depth times each agent's ``max_iterations``.
MAX_SUBAGENT_DEPTH: Final = 3


@runtime_checkable
class ChildAgentBuilder(Protocol):
    """Resolves an ``agent_ref`` and builds the referenced sub-agent.

    Injected into :class:`~orchestrator.tools.ToolEnv` by the
    control-plane — the orchestrator cannot resolve an ``agent_ref``
    itself (the ``AgentSpecStore`` is control-plane-only). :class:`SubAgentTool`
    calls this inside ``call()`` to obtain the child ``BuiltAgent`` it
    delegates to.

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
        non-deleted AgentSpec — :class:`SubAgentTool` lets that propagate
        so the parent's tools node turns it into a tool error rather than
        crashing the parent run.
        """


@dataclass(frozen=True)
class SubAgentTool:
    """A :class:`~orchestrator.tools.registry.Tool` that delegates to a
    deployed sub-agent — Stream J.4.

    One instance per ``SubAgentSpec`` declared in the parent manifest's
    ``spec.subagents`` block. ``call()`` builds the referenced child
    agent via :attr:`builder`, runs it to completion on a fresh thread
    seeded with the delegated ``task``, and returns the child's final
    answer as the tool result.

    The child run reuses the parent's :class:`CancellationToken` so a
    parent cancel propagates into every child node. A child that hits
    its own ``max_steps`` is **not** a tool error — its partial-progress
    note is returned as a normal :class:`ToolResult` so the parent can
    reason about it; a cancellation is left to propagate.
    """

    subagent: SubAgentSpec
    builder: ChildAgentBuilder
    #: The child's build-time recursion depth (parent depth + 1). Passed
    #: straight to :attr:`builder` so the depth cap is enforced there.
    child_depth: int

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.subagent.name,
            description=self.subagent.description,
            parameters={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "The subtask to delegate, described in full. The "
                            "sub-agent runs fresh with only this text as its "
                            "instruction — it sees none of this conversation."
                        ),
                    },
                },
                "required": ["task"],
            },
        )

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        if ctx.tenant_id is None:
            msg = (
                f"sub-agent {self.subagent.name!r} cannot be delegated to without a tenant binding"
            )
            raise ToolBlockedError(msg)
        task = self._require_task(args)
        name, version = parse_agent_ref(self.subagent.agent_ref)

        child = await self.builder(
            tenant_id=ctx.tenant_id,
            name=name,
            version=version,
            depth=self.child_depth,
        )
        child_config = self._child_config(ctx)
        child_input: dict[str, Any] = {
            "messages": [
                SystemMessage(content=child.system_prompt),
                HumanMessage(content=task),
            ],
            "step_count": 0,
            "max_steps": child.max_steps,
        }

        try:
            result = await child.graph.ainvoke(child_input, child_config)
        except MaxStepsExceededError:
            # A child that runs out of steps is a *partial result*, not a
            # tool failure — hand the parent a note so it can decide how
            # to proceed (vs. RunCancelledError, deliberately not caught:
            # a cancel tears the whole run down, so it must propagate).
            logger.info(
                "subagent.max_steps name=%s agent_ref=%s",
                self.subagent.name,
                self.subagent.agent_ref,
            )
            return ToolResult(
                content=(
                    f"[sub-agent {self.subagent.name!r} reached its step "
                    "limit before producing a final answer]"
                ),
                meta={"subagent_max_steps": True},
            )

        messages = result.get("messages", []) if isinstance(result, Mapping) else []
        answer = _final_answer(messages)
        if answer is None:
            return ToolResult(
                content=f"[sub-agent {self.subagent.name!r} produced no answer]",
                meta={"subagent_empty": True},
            )
        return ToolResult(content=answer, meta={"subagent": self.subagent.name})

    def _require_task(self, args: Mapping[str, Any]) -> str:
        raw = args.get("task")
        if not isinstance(raw, str) or not raw.strip():
            msg = f"sub-agent {self.subagent.name!r} requires a non-empty 'task' string"
            raise ValueError(msg)
        return raw.strip()

    def _child_config(self, ctx: ToolContext) -> RunnableConfig:
        """Build the child run's ``RunnableConfig``.

        The child gets a fresh ``thread_id`` / ``run_id`` (delegation is
        one-shot — the child run never resumes) but **shares the parent's**
        ``CancellationToken``, so a parent cancel reaches every child node.
        ``tenant_id`` / ``user_id`` carry over so the child's own tools
        stay tenant-scoped. Linking the child run to the parent for audit
        is the control-plane's job (Stream J.4 PR5).
        """
        token = ctx.cancellation_token or CancellationToken()
        configurable: dict[str, Any] = {
            CANCELLATION_TOKEN_KEY: token,
            "thread_id": str(uuid4()),
            "run_id": str(uuid4()),
            "tenant_id": str(ctx.tenant_id),
        }
        if ctx.user_id is not None:
            configurable["user_id"] = str(ctx.user_id)
        return {"configurable": configurable}


def _final_answer(messages: Sequence[BaseMessage]) -> str | None:
    """Return the last ``AIMessage``'s content as text, or ``None``.

    The child graph ends on the agent's no-tool-calls ``AIMessage``;
    that message's content is the delegated answer.
    """
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return None
