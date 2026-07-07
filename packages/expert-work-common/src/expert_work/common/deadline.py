"""Deadline hierarchy + cancellation — Stream A.13.

Design: subsystems/28-reliability-primitives § 3.3 / § 5.4.

Five-layer nested timeout per § 5.4::

    request > session > step > tool > llm

``DeadlineContext`` carries an **absolute** unix-ms deadline (not a
relative duration) so it survives serialization across services without
network-latency drift; the receiver re-computes ``remaining_ms`` against
its local clock. Each ``with_deadline()`` block derives a child with a
budget that is auto-clipped never to exceed its parent.

``CancelToken`` is the user-driven cancellation channel — orthogonal to
the time-based ``deadline_ms``. The most common reason a request ends
early is "user closed their tab" (cancel), not "took too long"
(deadline). The two live in the same context so any await point can
check either one.

This module is **framework-agnostic** by design — the FastAPI
middleware that injects the root ``DeadlineContext`` from
``X-Expert-Work-Deadline-Ms`` headers lands in Stream B, and the
``ExpertWorkHttpClient`` that propagates them outbound is also Stream B work.
A.13 ships the primitive that those layers wire up.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Final

_LAYER_NAMES: Final[frozenset[str]] = frozenset({"request", "session", "step", "tool", "llm"})


class DeadlineExceededError(TimeoutError):
    """Raised by :func:`deadline_check` when the current deadline has elapsed.

    Subclasses :class:`TimeoutError` so existing ``except TimeoutError``
    code paths still catch it.
    """

    def __init__(self, layer: str) -> None:
        super().__init__(f"deadline exceeded at layer={layer}")
        self.layer = layer


@dataclass
class CancelToken:
    """User-driven cancellation signal.

    Mutable on purpose — the FastAPI request-disconnect middleware
    (Stream B) calls :meth:`cancel` from a different task than the one
    running the request body. ``set()`` is idempotent.

    Distinct from :class:`asyncio.CancelledError`: a token can be flipped
    by an HTTP caller (via ``PATCH /v1/runs/{id}`` with a token ID) long
    before the request body's task actually awaits cancellation.
    """

    _event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def cancel(self) -> None:
        """Mark this token as cancelled. Idempotent."""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        """Snapshot of the cancellation flag."""
        return self._event.is_set()

    async def wait(self) -> None:
        """Suspend until :meth:`cancel` is called.

        Used by the outbound HTTP client / sandbox supervisor / LLM
        gateway to wake up the moment the user cancels (no busy-poll).
        """
        await self._event.wait()


class CancelledByUserError(RuntimeError):
    """Raised by :func:`deadline_check` when the cancel token is set.

    Distinct from :class:`DeadlineExceededError` so observability can
    split "timed out" vs "user-cancelled" cleanly (subsystems/20 § 5.2
    counters use ``outcome=cancelled`` vs ``outcome=timeout``).
    """


@dataclass(frozen=True)
class DeadlineContext:
    """One node in the deadline chain.

    Created either by :meth:`root` (process boundary; e.g., FastAPI
    middleware seeds it from the ``X-Expert-Work-Deadline-Ms`` header) or by
    :meth:`derived` (every child layer). Frozen so the chain is
    immutable — child nodes don't accidentally mutate their parent.

    The ``CancelToken`` reference is shared up the chain: cancelling at
    the request layer fires for every descendant.
    """

    deadline_ms: float
    layer: str
    cancel_token: CancelToken
    parent: DeadlineContext | None = None

    @classmethod
    def root(cls, max_ms: float, layer: str = "request") -> DeadlineContext:
        """Create a root context with deadline ``now + max_ms``."""
        _validate_layer(layer)
        return cls(
            deadline_ms=_now_ms() + max_ms,
            layer=layer,
            cancel_token=CancelToken(),
            parent=None,
        )

    @classmethod
    def from_absolute(
        cls,
        deadline_ms: float,
        *,
        layer: str = "request",
        cancel_token: CancelToken | None = None,
    ) -> DeadlineContext:
        """Seed from a header-supplied absolute deadline.

        Stream B's ASGI middleware uses this when an inbound request
        carries ``X-Expert-Work-Deadline-Ms`` from an upstream service.
        """
        _validate_layer(layer)
        return cls(
            deadline_ms=deadline_ms,
            layer=layer,
            cancel_token=cancel_token or CancelToken(),
            parent=None,
        )

    def remaining_ms(self) -> float:
        """Milliseconds left; never negative."""
        return max(0.0, self.deadline_ms - _now_ms())

    def derived(self, layer: str, max_ms: float) -> DeadlineContext:
        """Build a child context that **cannot outlive** ``self``.

        The clipping rule is the heart of the hierarchy: if the parent
        only has 5 s left and the child asks for 30 s, the child gets
        5 s. The :func:`with_deadline` contextmanager applies this and
        binds the contextvar.

        We bound the **absolute** child deadline by the parent's absolute
        deadline (rather than computing through ``remaining_ms()``) so a
        tiny clock advance between the two reads cannot let the child
        slip past the parent by a fraction of a millisecond.

        :raises ValueError: ``layer`` is not one of the canonical five.
        """
        _validate_layer(layer)
        desired = _now_ms() + max_ms
        return DeadlineContext(
            deadline_ms=min(desired, self.deadline_ms),
            layer=layer,
            cancel_token=self.cancel_token,
            parent=self,
        )


_current_deadline: ContextVar[DeadlineContext | None] = ContextVar(
    "expert_work_current_deadline",
    default=None,
)


def get_current_deadline() -> DeadlineContext | None:
    """Return the active deadline, or ``None`` outside any
    :func:`with_deadline` block (background jobs, healthcheck handlers)."""
    return _current_deadline.get()


@asynccontextmanager
async def with_deadline(
    layer: str,
    max_ms: float,
    *,
    cancel_token: CancelToken | None = None,
) -> AsyncIterator[DeadlineContext]:
    """Open a deadline scope; auto-clip against the parent if any.

    Typical use::

        async with with_deadline("step", 60_000) as ctx:
            await deadline_check()
            ...

    When ``cancel_token`` is supplied **and** there is no parent, that
    token becomes the root. Inside a nested scope the parent's token
    always wins (the chain shares one source of cancellation).
    """
    parent = _current_deadline.get()
    if parent is not None:
        ctx = parent.derived(layer, max_ms)
    elif cancel_token is not None:
        _validate_layer(layer)
        ctx = DeadlineContext(
            deadline_ms=_now_ms() + max_ms,
            layer=layer,
            cancel_token=cancel_token,
            parent=None,
        )
    else:
        ctx = DeadlineContext.root(max_ms, layer)

    token = _current_deadline.set(ctx)
    try:
        yield ctx
    finally:
        _current_deadline.reset(token)


async def deadline_check() -> None:
    """Raise if the active deadline expired or the cancel token fired.

    Drop this at the head of every long-running ``async`` body that
    matters (LangGraph node entry, tool dispatcher, LLM streaming
    callback). Cheap: ``time.time()`` + dict read.
    """
    ctx = _current_deadline.get()
    if ctx is None:
        return
    if ctx.cancel_token.cancelled:
        raise CancelledByUserError(f"cancelled at layer={ctx.layer}")
    if ctx.remaining_ms() <= 0:
        raise DeadlineExceededError(ctx.layer)


# Helper exposed for tests that need to drive the clock deterministically.
def _now_ms() -> float:
    """Current unix time in milliseconds. Wrapper exists so tests can
    monkeypatch without reaching into ``time``."""
    return time.time() * 1000


def _validate_layer(layer: str) -> None:
    if layer not in _LAYER_NAMES:
        valid = ", ".join(sorted(_LAYER_NAMES))
        msg = (
            f"unknown deadline layer {layer!r}; must be one of: {valid}. "
            "See subsystems/28-reliability-primitives § 5.4."
        )
        raise ValueError(msg)
