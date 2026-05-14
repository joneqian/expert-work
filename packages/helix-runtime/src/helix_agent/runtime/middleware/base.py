"""Core types for the orchestrator middleware chain.

Per [STREAM-E-DESIGN § 2.2](../../../../../../../docs/streams/STREAM-E-DESIGN.md),
M0 exposes four fixed anchor points (see :data:`ANCHORS`). Concrete
middlewares (dynamic_context, llm_error_handling, langfuse, pii_redact,
sandbox_audit, llm_response_cache_*) land in subsequent Stream E PRs;
this module only defines the contract.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

#: Fixed set of anchor names. Middleware must declare ``anchor`` ∈ this set
#: or :class:`UnknownAnchorError` is raised at chain construction.
#:
#: - ``before_llm_call``    — preparing LLM payload (dynamic_context, pii_redact, cache lookup)
#: - ``around_llm_call``    — wrapping the LLM call itself (error_handling, langfuse)
#: - ``after_llm_call``     — post-LLM, pre-ReAct-loop (cache store, langfuse)
#: - ``before_tool_dispatch`` — args ready, before calling the tool (sandbox_audit)
ANCHORS: frozenset[str] = frozenset(
    {
        "before_llm_call",
        "around_llm_call",
        "after_llm_call",
        "before_tool_dispatch",
    }
)


@dataclass
class MiddlewareContext:
    """Mutable payload threaded through one chain invocation.

    M0 intentionally exposes only a free-form ``payload`` dict so each
    Stream E sub-PR can add the fields it needs without churning every
    other middleware. Concrete typed fields move in here as the chain
    matures and access patterns stabilise.
    """

    payload: dict[str, Any] = field(default_factory=dict)


#: Signature of the ``call_next`` callable a middleware receives. Calling it
#: invokes the next layer (or the terminal handler at the end of the chain).
CallNext = Callable[[MiddlewareContext], Awaitable[None]]


@runtime_checkable
class Middleware(Protocol):
    """The contract every middleware must satisfy.

    Implementations declare ordering with ``after`` / ``before`` tuples of
    peer ``name`` values; the chain topologically sorts at construction.
    Unknown names in ``after`` / ``before`` are silently ignored — this
    lets a later Stream E PR add a middleware that declares a dependency
    on one that hasn't shipped yet without breaking the chain.

    ``anchor`` selects which of the four M0 anchors this middleware
    registers to. A middleware that ought to run at two anchors (e.g.,
    Langfuse spans both ``around_llm_call`` and ``after_llm_call``) is
    expressed as two distinct middleware instances.
    """

    name: str
    anchor: str
    after: tuple[str, ...]
    before: tuple[str, ...]

    async def __call__(self, ctx: MiddlewareContext, call_next: CallNext) -> None:
        """Run the middleware body; call ``call_next(ctx)`` once to continue
        the chain. Pre-``call_next`` logic runs before downstream middlewares
        and the terminal; post-``call_next`` logic runs after they unwind."""
