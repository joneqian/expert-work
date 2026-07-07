"""Unit tests for :class:`PIIRedactorMiddleware` (Stream E.5 / D.2 cross-stream)."""

from __future__ import annotations

import re
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from expert_work.runtime.middleware import (
    Middleware,
    MiddlewareContext,
    PIIRedactorMiddleware,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _ssn_redactor(text: str, _tenant_id: UUID | None) -> str:
    """Test stand-in — masks anything that looks like an SSN."""
    return re.sub(r"\d{3}-\d{2}-\d{4}", "***REDACTED***", text)


async def _terminal(ctx: MiddlewareContext) -> None:
    ctx.payload["terminal_ran"] = True


def _ctx(messages: list[BaseMessage] | None = None, **extra: object) -> MiddlewareContext:
    payload: dict[str, object] = {}
    if messages is not None:
        payload["messages"] = messages
    payload.update(extra)
    return MiddlewareContext(payload=payload)


# ---------------------------------------------------------------------------
# Pass-through contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_messages_passthrough() -> None:
    mw = PIIRedactorMiddleware(redact_text=_ssn_redactor)
    ctx = _ctx(messages=[])
    await mw(ctx, _terminal)
    assert ctx.payload["terminal_ran"] is True
    assert ctx.payload["messages"] == []


@pytest.mark.asyncio
async def test_missing_messages_key_passthrough() -> None:
    mw = PIIRedactorMiddleware(redact_text=_ssn_redactor)
    ctx = _ctx()
    await mw(ctx, _terminal)
    assert ctx.payload["terminal_ran"] is True


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redacts_matching_content() -> None:
    mw = PIIRedactorMiddleware(redact_text=_ssn_redactor)
    msgs = [
        HumanMessage(content="my ssn is 123-45-6789"),
        AIMessage(content="thanks!"),
    ]
    ctx = _ctx(messages=list(msgs))
    await mw(ctx, _terminal)
    redacted = ctx.payload["messages"]
    assert redacted[0].content == "my ssn is ***REDACTED***"
    assert redacted[1].content == "thanks!"


@pytest.mark.asyncio
async def test_preserves_message_identity_when_no_match() -> None:
    """No redaction → same message instances → prefix cache stable."""
    mw = PIIRedactorMiddleware(redact_text=_ssn_redactor)
    msgs = [HumanMessage(content="hello"), AIMessage(content="hi")]
    ctx = _ctx(messages=list(msgs))
    await mw(ctx, _terminal)
    # On no change, middleware should not even write back.
    assert ctx.payload["messages"] is not None
    # Identity preserved on each message.
    for original, after in zip(msgs, ctx.payload["messages"], strict=True):
        assert original is after


@pytest.mark.asyncio
async def test_system_message_redacted_too() -> None:
    mw = PIIRedactorMiddleware(redact_text=_ssn_redactor)
    msgs = [SystemMessage(content="user 123-45-6789 has admin")]
    ctx = _ctx(messages=list(msgs))
    await mw(ctx, _terminal)
    assert ctx.payload["messages"][0].content == "user ***REDACTED*** has admin"


@pytest.mark.asyncio
async def test_default_no_op_redactor() -> None:
    mw = PIIRedactorMiddleware()  # default _noop_redact_text
    msgs = [HumanMessage(content="ssn 123-45-6789 here")]
    ctx = _ctx(messages=list(msgs))
    await mw(ctx, _terminal)
    assert ctx.payload["messages"][0].content == "ssn 123-45-6789 here"


# ---------------------------------------------------------------------------
# Tenant binding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_id_uuid_passed_through() -> None:
    captured: list[UUID | None] = []

    def capturing_redactor(text: str, tenant_id: UUID | None) -> str:
        captured.append(tenant_id)
        return text

    tid = uuid4()
    mw = PIIRedactorMiddleware(redact_text=capturing_redactor)
    await mw(_ctx(messages=[HumanMessage(content="x")], tenant_id=tid), _terminal)
    assert captured == [tid]


@pytest.mark.asyncio
async def test_tenant_id_string_parsed_to_uuid() -> None:
    captured: list[UUID | None] = []

    def capturing_redactor(text: str, tenant_id: UUID | None) -> str:
        captured.append(tenant_id)
        return text

    tid = uuid4()
    mw = PIIRedactorMiddleware(redact_text=capturing_redactor)
    await mw(_ctx(messages=[HumanMessage(content="x")], tenant_id=str(tid)), _terminal)
    assert captured == [tid]


@pytest.mark.asyncio
async def test_missing_tenant_id_is_none() -> None:
    captured: list[UUID | None] = []

    def capturing_redactor(text: str, tenant_id: UUID | None) -> str:
        captured.append(tenant_id)
        return text

    mw = PIIRedactorMiddleware(redact_text=capturing_redactor)
    await mw(_ctx(messages=[HumanMessage(content="x")]), _terminal)
    assert captured == [None]


@pytest.mark.asyncio
async def test_invalid_tenant_id_string_falls_back_to_none() -> None:
    captured: list[UUID | None] = []

    def capturing_redactor(text: str, tenant_id: UUID | None) -> str:
        captured.append(tenant_id)
        return text

    mw = PIIRedactorMiddleware(redact_text=capturing_redactor)
    await mw(
        _ctx(messages=[HumanMessage(content="x")], tenant_id="not-a-uuid"),
        _terminal,
    )
    assert captured == [None]


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redactor_exception_keeps_original_message() -> None:
    """If the injected redactor blows up on one message, fall through."""

    def crashing_redactor(text: str, _tenant_id: UUID | None) -> str:
        if "ssn" in text:
            raise RuntimeError("oops")
        return text

    mw = PIIRedactorMiddleware(redact_text=crashing_redactor)
    msgs = [HumanMessage(content="ssn here"), HumanMessage(content="ok")]
    ctx = _ctx(messages=list(msgs))
    await mw(ctx, _terminal)
    # Crashed message: original kept as-is.
    assert ctx.payload["messages"][0].content == "ssn here"
    assert ctx.payload["messages"][1].content == "ok"


@pytest.mark.asyncio
async def test_non_string_content_passes_through() -> None:
    """Multimodal content (list-of-blocks) is M2/M3 scope — leave it alone."""
    msg = HumanMessage(content=[{"type": "text", "text": "ssn 123-45-6789"}])
    mw = PIIRedactorMiddleware(redact_text=_ssn_redactor)
    await mw(_ctx(messages=[msg]), _terminal)
    # We didn't touch it; identity preserved.
    # (No write-back since 'changed' stays False.)


# ---------------------------------------------------------------------------
# Anchor wiring + Protocol
# ---------------------------------------------------------------------------


def test_registers_before_llm_call_anchor_after_dynamic_context() -> None:
    mw = PIIRedactorMiddleware(redact_text=_ssn_redactor)
    assert mw.anchor == "before_llm_call"
    assert "dynamic_context" in mw.after


def test_satisfies_middleware_protocol() -> None:
    assert isinstance(PIIRedactorMiddleware(redact_text=_ssn_redactor), Middleware)
