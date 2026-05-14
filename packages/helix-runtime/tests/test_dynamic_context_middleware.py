"""Unit tests for :class:`DynamicContextMiddleware` (Stream E.3).

Token estimator is the default 4-char heuristic unless a test overrides
it. ``HumanMessage(content="x" * 4000)`` therefore counts as 1000 tokens.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from helix_agent.runtime.middleware import (
    DynamicContextMiddleware,
    Middleware,
    MiddlewareChain,
    MiddlewareContext,
    default_token_estimator,
)


def _msg(role: str, length: int) -> BaseMessage:
    """Build a message of approximately ``length`` characters (≈ length/4 tokens)."""
    content = "x" * length
    if role == "human":
        return HumanMessage(content=content)
    if role == "ai":
        return AIMessage(content=content)
    if role == "system":
        return SystemMessage(content=content)
    raise ValueError(f"unknown role: {role!r}")


def _ctx(messages: list[BaseMessage]) -> MiddlewareContext:
    return MiddlewareContext(payload={"messages": messages})


async def _terminal(ctx: MiddlewareContext) -> None:
    ctx.payload["terminal_called"] = ctx.payload.get("terminal_called", 0) + 1


# ---------------------------------------------------------------------------
# Pass-through contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_messages_passthrough() -> None:
    mw = DynamicContextMiddleware()
    ctx = _ctx([])
    await mw(ctx, _terminal)
    assert ctx.payload["terminal_called"] == 1
    assert ctx.payload["messages"] == []


@pytest.mark.asyncio
async def test_no_trim_under_budget() -> None:
    """5 turns x 100 chars (~25 tokens each) is well under defaults — untouched."""
    mw = DynamicContextMiddleware()
    messages = [_msg("human" if i % 2 == 0 else "ai", 100) for i in range(5)]
    ctx = _ctx(list(messages))
    await mw(ctx, _terminal)
    assert ctx.payload["messages"] == messages


# ---------------------------------------------------------------------------
# Trim by max_turns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trim_by_max_turns_keeps_newest() -> None:
    mw = DynamicContextMiddleware(max_turns=5, max_tokens=1_000_000)
    messages = [_msg("human", 10) for _ in range(30)]
    ctx = _ctx(messages)
    await mw(ctx, _terminal)
    trimmed = ctx.payload["messages"]
    assert len(trimmed) == 5
    # The kept five must be the newest (last) five, identity-preserved.
    assert trimmed == messages[-5:]


# ---------------------------------------------------------------------------
# Trim by max_tokens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trim_by_max_tokens_keeps_newest_within_budget() -> None:
    """10 messages of 4000 chars ≈ 1000 tokens each; budget 3500 → keep last 3."""
    mw = DynamicContextMiddleware(max_turns=100, max_tokens=3500)
    messages = [_msg("human" if i % 2 == 0 else "ai", 4000) for i in range(10)]
    ctx = _ctx(messages)
    await mw(ctx, _terminal)
    trimmed = ctx.payload["messages"]
    assert trimmed == messages[-3:]
    total = sum(default_token_estimator(m) for m in trimmed)
    assert total <= 3500


@pytest.mark.asyncio
async def test_max_turns_cap_runs_before_token_budget() -> None:
    """max_turns is the harder ceiling — even with infinite budget it bites."""
    mw = DynamicContextMiddleware(max_turns=3, max_tokens=1_000_000)
    messages = [_msg("human", 100) for _ in range(20)]
    ctx = _ctx(messages)
    await mw(ctx, _terminal)
    assert len(ctx.payload["messages"]) == 3


# ---------------------------------------------------------------------------
# System message handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_message_preserved_through_trim() -> None:
    """SystemMessage must survive max_turns + max_tokens trimming.

    Dropping it would change the prompt prefix and break Anthropic's
    prefix-cache on every subsequent call.
    """
    sys_msg = _msg("system", 200)  # ~50 tokens
    regular = [_msg("human" if i % 2 == 0 else "ai", 4000) for i in range(20)]
    mw = DynamicContextMiddleware(max_turns=5, max_tokens=2000)
    ctx = _ctx([sys_msg, *regular])
    await mw(ctx, _terminal)

    trimmed = ctx.payload["messages"]
    assert trimmed[0] is sys_msg
    # The remainder is the most recent fitting under budget.
    assert all(m in regular for m in trimmed[1:])
    assert trimmed[1:] == regular[-len(trimmed[1:]) :]


@pytest.mark.asyncio
async def test_system_only_payload_passes_through() -> None:
    sys_msg = _msg("system", 100)
    mw = DynamicContextMiddleware(max_tokens=50)
    ctx = _ctx([sys_msg])
    await mw(ctx, _terminal)
    assert ctx.payload["messages"] == [sys_msg]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_oversized_message_kept() -> None:
    """If the newest message alone exceeds budget, keep it anyway.

    Sending an oversized prompt and letting the provider surface the
    error beats sending an empty conversation that the model can't
    respond to at all.
    """
    huge = _msg("human", 80_000)  # ~20000 tokens
    mw = DynamicContextMiddleware(max_tokens=1000)
    ctx = _ctx([huge])
    await mw(ctx, _terminal)
    assert ctx.payload["messages"] == [huge]


@pytest.mark.asyncio
async def test_custom_token_estimator_wired_through() -> None:
    """Caller-supplied estimator overrides the default 4-char heuristic."""
    # 1 token per message regardless of content size.
    mw = DynamicContextMiddleware(
        max_tokens=3,
        token_estimator=lambda _msg: 1,
    )
    messages = [_msg("human", 100_000) for _ in range(10)]
    ctx = _ctx(messages)
    await mw(ctx, _terminal)
    assert len(ctx.payload["messages"]) == 3


@pytest.mark.asyncio
async def test_default_token_estimator_rounds_up_to_minimum_one() -> None:
    """Empty-content message still counts as 1 token, not 0."""
    empty = HumanMessage(content="")
    assert default_token_estimator(empty) == 1


# ---------------------------------------------------------------------------
# MiddlewareChain integration smoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registers_to_before_llm_call_anchor() -> None:
    mw = DynamicContextMiddleware(max_turns=2, max_tokens=1_000_000)
    chain = MiddlewareChain.from_middlewares("before_llm_call", [mw])
    assert chain.ordered_names == ("dynamic_context",)

    messages = [_msg("human", 10) for _ in range(5)]
    ctx = _ctx(messages)
    await chain.invoke(ctx, _terminal)
    assert len(ctx.payload["messages"]) == 2


def test_satisfies_middleware_protocol() -> None:
    assert isinstance(DynamicContextMiddleware(), Middleware)
