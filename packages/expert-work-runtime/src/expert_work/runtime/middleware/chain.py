"""Topologically-ordered middleware chain bound to a single anchor.

The chain is built once at boot (or test fixture setup) and reused for
every invocation; per-invocation cost is one ``terminal`` wrap pass
proportional to the number of middlewares in the chain.

Build via :meth:`MiddlewareChain.from_middlewares` — direct ``__init__``
takes the pre-ordered tuple and skips validation, suited for callers
that already know the order.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from expert_work.runtime.middleware.base import (
    ANCHORS,
    CallNext,
    Middleware,
    MiddlewareContext,
)
from expert_work.runtime.middleware.errors import (
    ChainCycleError,
    DuplicateMiddlewareError,
    UnknownAnchorError,
)

logger = logging.getLogger(__name__)


class MiddlewareChain:
    """Ordered sequence of middlewares for one anchor.

    Construction validates:

    1. ``anchor`` is in :data:`expert_work.runtime.middleware.base.ANCHORS`
    2. Middleware ``name`` values are unique within the anchor
    3. ``after`` / ``before`` declarations don't form a cycle

    Filters to middlewares whose ``anchor`` matches — passing a mixed
    list of middlewares (the typical case when boot collects them all)
    is supported; foreign-anchor entries are silently skipped.

    Invocation walks the ordered list and wraps the user-supplied
    ``terminal`` callable so that each middleware sees ``call_next``
    pointing at the next-in-order (or ``terminal`` at the tail).
    """

    __slots__ = ("_anchor", "_ordered")

    def __init__(self, anchor: str, ordered: tuple[Middleware, ...]) -> None:
        if anchor not in ANCHORS:
            msg = f"unknown anchor {anchor!r}; valid: {sorted(ANCHORS)}"
            raise UnknownAnchorError(msg)
        self._anchor = anchor
        self._ordered = ordered

    @property
    def anchor(self) -> str:
        return self._anchor

    @property
    def ordered_names(self) -> tuple[str, ...]:
        """Names of middlewares in execution order — useful for assertions."""
        return tuple(m.name for m in self._ordered)

    @classmethod
    def from_middlewares(
        cls,
        anchor: str,
        middlewares: Sequence[Middleware],
    ) -> MiddlewareChain:
        """Build a chain for ``anchor`` from a mixed list of middlewares.

        Filters by anchor, checks for duplicate names, topologically
        sorts. Raises :class:`UnknownAnchorError`,
        :class:`DuplicateMiddlewareError`, or :class:`ChainCycleError` on
        configuration problems.
        """
        if anchor not in ANCHORS:
            msg = f"unknown anchor {anchor!r}; valid: {sorted(ANCHORS)}"
            raise UnknownAnchorError(msg)

        scoped = [m for m in middlewares if m.anchor == anchor]

        names: set[str] = set()
        for m in scoped:
            if m.name in names:
                msg = f"duplicate middleware name {m.name!r} in anchor {anchor!r}"
                raise DuplicateMiddlewareError(msg)
            names.add(m.name)

        ordered = _topological_sort(scoped)
        logger.debug(
            "middleware.chain.built anchor=%s order=%s",
            anchor,
            [m.name for m in ordered],
        )
        return cls(anchor, ordered)

    async def invoke(
        self,
        ctx: MiddlewareContext,
        terminal: CallNext,
    ) -> None:
        """Execute the chain, with ``terminal`` invoked after the last middleware.

        Empty chain → ``terminal`` is called directly.
        """
        handler: CallNext = terminal
        for mw in reversed(self._ordered):
            handler = _wrap(mw, handler)
        await handler(ctx)


def _wrap(mw: Middleware, next_handler: CallNext) -> CallNext:
    """Bind ``mw`` and ``next_handler`` into a fresh closure.

    Pulled out to avoid the Python closure late-binding gotcha that
    would otherwise capture only the last loop iteration's variables.
    """

    async def inner(ctx: MiddlewareContext) -> None:
        await mw(ctx, next_handler)

    return inner


def _topological_sort(middlewares: Sequence[Middleware]) -> tuple[Middleware, ...]:
    """Kahn's algorithm with alphabetic tie-break.

    Predecessor set ``preds[name]`` lists the middlewares that must run
    before ``name``. Unknown dependency names (``after`` / ``before``
    referencing a middleware not in this anchor) are silently dropped —
    declaring a soft dependency on a not-yet-shipped middleware is a
    feature, not a bug (lets later PRs slot in cleanly).
    """
    by_name = {m.name: m for m in middlewares}
    preds: dict[str, set[str]] = {m.name: set() for m in middlewares}

    for m in middlewares:
        for dep in m.after:
            if dep in by_name:
                preds[m.name].add(dep)
        for dep in m.before:
            if dep in by_name:
                preds[dep].add(m.name)

    ordered: list[Middleware] = []
    ready = sorted(name for name, p in preds.items() if not p)

    while ready:
        # Pop the alphabetically smallest ready node for stable output.
        name = ready.pop(0)
        ordered.append(by_name[name])
        del preds[name]
        for other, other_preds in preds.items():
            if name in other_preds:
                other_preds.discard(name)
                if not other_preds and other not in ready:
                    ready.append(other)
        ready.sort()

    if preds:
        remaining = sorted(preds.keys())
        msg = f"cycle in middleware chain among: {remaining}"
        raise ChainCycleError(msg)

    return tuple(ordered)
