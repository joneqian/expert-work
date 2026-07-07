"""Unit tests for the orchestrator middleware chain (Stream E.2).

The chain is pure infrastructure — no concrete middleware ships yet —
so the tests use lightweight ``RecordingMiddleware`` fixtures that log
their entry / exit into the shared payload.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from expert_work.runtime.middleware import (
    CallNext,
    ChainCycleError,
    DuplicateMiddlewareError,
    Middleware,
    MiddlewareChain,
    MiddlewareContext,
    UnknownAnchorError,
)


@dataclass
class RecordingMiddleware:
    """Test fixture: records ``"{name}:enter"`` / ``"{name}:exit"`` to ``ctx.payload["log"]``."""

    name: str
    anchor: str = "before_llm_call"
    after: tuple[str, ...] = ()
    before: tuple[str, ...] = ()

    async def __call__(self, ctx: MiddlewareContext, call_next: CallNext) -> None:
        ctx.payload.setdefault("log", []).append(f"{self.name}:enter")
        await call_next(ctx)
        ctx.payload["log"].append(f"{self.name}:exit")


def _ctx() -> MiddlewareContext:
    return MiddlewareContext()


async def _terminal(ctx: MiddlewareContext) -> None:
    ctx.payload.setdefault("log", []).append("terminal")


# ---------------------------------------------------------------------------
# Basic invocation contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_chain_calls_terminal_once() -> None:
    chain = MiddlewareChain.from_middlewares("before_llm_call", [])
    ctx = _ctx()
    await chain.invoke(ctx, _terminal)
    assert ctx.payload["log"] == ["terminal"]


@pytest.mark.asyncio
async def test_single_middleware_wraps_terminal() -> None:
    chain = MiddlewareChain.from_middlewares(
        "before_llm_call",
        [RecordingMiddleware(name="solo")],
    )
    ctx = _ctx()
    await chain.invoke(ctx, _terminal)
    assert ctx.payload["log"] == ["solo:enter", "terminal", "solo:exit"]


# ---------------------------------------------------------------------------
# Ordering by ``after`` / ``before``
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_relation_orders_dependent_first() -> None:
    """A.before=(B,) → A runs before B."""
    chain = MiddlewareChain.from_middlewares(
        "before_llm_call",
        [
            RecordingMiddleware(name="b"),
            RecordingMiddleware(name="a", before=("b",)),
        ],
    )
    assert chain.ordered_names == ("a", "b")


@pytest.mark.asyncio
async def test_after_relation_orders_dependency_first() -> None:
    """A.after=(B,) → B runs before A."""
    chain = MiddlewareChain.from_middlewares(
        "before_llm_call",
        [
            RecordingMiddleware(name="a", after=("b",)),
            RecordingMiddleware(name="b"),
        ],
    )
    assert chain.ordered_names == ("b", "a")


@pytest.mark.asyncio
async def test_transitive_dependencies() -> None:
    """A before=(B,), B before=(C,) → A, B, C."""
    chain = MiddlewareChain.from_middlewares(
        "before_llm_call",
        [
            RecordingMiddleware(name="c"),
            RecordingMiddleware(name="a", before=("b",)),
            RecordingMiddleware(name="b", before=("c",)),
        ],
    )
    assert chain.ordered_names == ("a", "b", "c")


@pytest.mark.asyncio
async def test_independent_peers_sort_alphabetically() -> None:
    """No-dependency peers fall back to alphabetic order — stable across runs."""
    chain = MiddlewareChain.from_middlewares(
        "before_llm_call",
        [
            RecordingMiddleware(name="charlie"),
            RecordingMiddleware(name="alpha"),
            RecordingMiddleware(name="bravo"),
        ],
    )
    assert chain.ordered_names == ("alpha", "bravo", "charlie")


@pytest.mark.asyncio
async def test_invocation_runs_in_declared_order() -> None:
    chain = MiddlewareChain.from_middlewares(
        "before_llm_call",
        [
            RecordingMiddleware(name="b", after=("a",)),
            RecordingMiddleware(name="a"),
        ],
    )
    ctx = _ctx()
    await chain.invoke(ctx, _terminal)
    assert ctx.payload["log"] == [
        "a:enter",
        "b:enter",
        "terminal",
        "b:exit",
        "a:exit",
    ]


# ---------------------------------------------------------------------------
# Configuration errors
# ---------------------------------------------------------------------------


def test_cycle_raises_chaincycleerror() -> None:
    with pytest.raises(ChainCycleError) as excinfo:
        MiddlewareChain.from_middlewares(
            "before_llm_call",
            [
                RecordingMiddleware(name="a", after=("b",)),
                RecordingMiddleware(name="b", after=("a",)),
            ],
        )
    assert "a" in str(excinfo.value)
    assert "b" in str(excinfo.value)


def test_duplicate_name_raises_duplicatemiddlewareerror() -> None:
    with pytest.raises(DuplicateMiddlewareError):
        MiddlewareChain.from_middlewares(
            "before_llm_call",
            [
                RecordingMiddleware(name="dup"),
                RecordingMiddleware(name="dup"),
            ],
        )


def test_unknown_anchor_raises_unknownanchorerror() -> None:
    with pytest.raises(UnknownAnchorError):
        MiddlewareChain.from_middlewares(
            "before_random_thing",
            [RecordingMiddleware(name="a")],
        )


# ---------------------------------------------------------------------------
# Anchor filtering + soft dependencies
# ---------------------------------------------------------------------------


def test_chain_filters_by_anchor() -> None:
    """Mixed-anchor input → only matching anchor enters the chain."""
    chain = MiddlewareChain.from_middlewares(
        "before_llm_call",
        [
            RecordingMiddleware(name="here", anchor="before_llm_call"),
            RecordingMiddleware(name="elsewhere", anchor="around_llm_call"),
        ],
    )
    assert chain.ordered_names == ("here",)


def test_unknown_dependency_name_silently_skipped() -> None:
    """``before`` / ``after`` referencing a non-present name is dropped.

    Lets a later PR add a middleware that declares a soft dependency on
    one that hasn't shipped yet — no startup failure.
    """
    chain = MiddlewareChain.from_middlewares(
        "before_llm_call",
        [
            RecordingMiddleware(name="a", before=("not_yet_shipped",)),
        ],
    )
    assert chain.ordered_names == ("a",)


def test_runtime_checkable_middleware_protocol() -> None:
    """A duck-typed object that matches the contract passes ``isinstance``."""
    mw = RecordingMiddleware(name="duck")
    assert isinstance(mw, Middleware)
