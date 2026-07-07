"""Unit tests for :class:`ContextPressureMiddleware` (3.3).

Default 4-char estimator: ``HumanMessage(content="x" * N)`` ≈ N/4 tokens.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from expert_work.runtime.middleware import (
    ContextPressureMiddleware,
    MiddlewareContext,
)

_NOTE_MARK = "Context budget:"


def _human(length: int) -> BaseMessage:
    return HumanMessage(content="x" * length)


def _ctx(messages: list[BaseMessage]) -> MiddlewareContext:
    return MiddlewareContext(payload={"messages": messages})


async def _terminal(ctx: MiddlewareContext) -> None:
    ctx.payload["terminal_called"] = ctx.payload.get("terminal_called", 0) + 1


def _last_text(ctx: MiddlewareContext) -> str:
    msg = ctx.payload["messages"][-1]
    return msg.content if isinstance(msg.content, str) else str(msg.content)


# --- threshold gating -------------------------------------------------------


@pytest.mark.asyncio
async def test_below_threshold_no_injection() -> None:
    # 400 chars ≈ 100 tokens; window 1000 → 10% used, below 75%.
    msgs = [SystemMessage(content="sys"), _human(400)]
    ctx = _ctx(msgs)
    mw = ContextPressureMiddleware(context_window=1000)
    await mw(ctx, _terminal)
    assert _NOTE_MARK not in _last_text(ctx)
    assert ctx.payload["messages"] == msgs  # untouched
    assert ctx.payload["terminal_called"] == 1


@pytest.mark.asyncio
async def test_at_or_above_threshold_injects_note() -> None:
    # 3200 chars ≈ 800 tokens; window 1000 → 80% used, above 75%.
    ctx = _ctx([SystemMessage(content="sys"), _human(3200)])
    mw = ContextPressureMiddleware(context_window=1000)
    await mw(ctx, _terminal)
    text = _last_text(ctx)
    assert _NOTE_MARK in text
    assert "80% used" in text


@pytest.mark.asyncio
async def test_prefix_preserved_only_last_changes() -> None:
    sys_msg = SystemMessage(content="system anchor")
    mid = AIMessage(content="y" * 100)
    ctx = _ctx([sys_msg, mid, _human(3600)])
    mw = ContextPressureMiddleware(context_window=1000)
    await mw(ctx, _terminal)
    out = ctx.payload["messages"]
    assert out[0] is sys_msg  # leading messages identical objects → prefix cache safe
    assert out[1] is mid
    assert _NOTE_MARK in _last_text(ctx)


# --- robustness -------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_messages_pass_through() -> None:
    ctx = _ctx([])
    mw = ContextPressureMiddleware(context_window=1000)
    await mw(ctx, _terminal)
    assert ctx.payload["terminal_called"] == 1


@pytest.mark.asyncio
async def test_nonpositive_window_disables() -> None:
    ctx = _ctx([_human(8000)])
    mw = ContextPressureMiddleware(context_window=0)
    await mw(ctx, _terminal)
    assert _NOTE_MARK not in _last_text(ctx)


@pytest.mark.asyncio
async def test_list_content_appends_text_block() -> None:
    msg = HumanMessage(content=[{"type": "text", "text": "x" * 3600}])
    ctx = _ctx([msg])
    mw = ContextPressureMiddleware(context_window=1000)
    await mw(ctx, _terminal)
    blocks = ctx.payload["messages"][-1].content
    assert isinstance(blocks, list)
    assert blocks[-1]["type"] == "text" and _NOTE_MARK in blocks[-1]["text"]


@pytest.mark.asyncio
async def test_custom_estimator_used() -> None:
    # An estimator that reports a huge count forces the note regardless of length.
    ctx = _ctx([_human(4)])
    mw = ContextPressureMiddleware(context_window=1000, token_estimator=lambda _m: 900)
    await mw(ctx, _terminal)
    assert _NOTE_MARK in _last_text(ctx)


@pytest.mark.asyncio
async def test_remaining_tokens_reported() -> None:
    # 3600 chars ≈ 900 tokens; window 1000 → remaining ~100.
    ctx = _ctx([_human(3600)])
    mw = ContextPressureMiddleware(context_window=1000)
    await mw(ctx, _terminal)
    assert "of 1000 tokens remaining" in _last_text(ctx)
    assert "~100 of 1000" in _last_text(ctx)
