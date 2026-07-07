"""Errors raised when building or invoking a :class:`MiddlewareChain`.

Per [STREAM-E-DESIGN § 3 Mini-ADR E-2](../../../../../../../docs/streams/STREAM-E-DESIGN.md),
configuration errors are surfaced at chain construction (boot time) rather
than at the first request — that way a misconfigured stack fails startup
loud instead of dropping into prod with a partial chain.
"""

from __future__ import annotations


class MiddlewareError(Exception):
    """Base class for middleware-chain configuration errors."""


class UnknownAnchorError(MiddlewareError):
    """Raised when a chain or middleware references an anchor name not in
    :data:`expert_work.runtime.middleware.base.ANCHORS`.

    Anchors are fixed in M0 (see ADR E-2); a typo or invented anchor is
    a programmer error, never a config-time decision.
    """


class DuplicateMiddlewareError(MiddlewareError):
    """Raised when two middlewares in the same anchor share a ``name``.

    Names must be unique within an anchor — they are referenced by
    ``after`` / ``before`` declarations on peer middlewares to express
    ordering, so duplicates would silently collide.
    """


class ChainCycleError(MiddlewareError):
    """Raised when the topological sort of an anchor's middlewares finds
    a cycle in the ``after`` / ``before`` declarations.

    The exception message lists the remaining unresolved names so the
    offending pair is easy to find.
    """
