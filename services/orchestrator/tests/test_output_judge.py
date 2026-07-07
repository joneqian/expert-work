"""Unit tests for the PI-2b output-judge seam + LLM judge."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from expert_work.protocol import StructuredOutputSpec
from expert_work.runtime.middleware import LLMOutputValidationError
from orchestrator.output_judge import (
    ActionVerdict,
    FakeActionJudge,
    FakeOutputJudge,
    LLMActionJudge,
    LLMOutputJudge,
    OutputJudgeVerdict,
)


@dataclass
class _FakeCaller:
    """Returns a canned reply; records the last messages + schema it saw.

    ``parsed`` (when set) rides on ``additional_kwargs["parsed"]`` — the
    RT-1 router contract for a validated structured response.
    """

    reply: str
    parsed: dict[str, object] | None = None
    seen: list[BaseMessage] | None = None
    seen_schema: StructuredOutputSpec | None = None

    async def __call__(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[object],
        output_schema: StructuredOutputSpec | None = None,
    ) -> AIMessage:
        del tools
        self.seen = list(messages)
        self.seen_schema = output_schema
        kwargs = {"parsed": self.parsed} if self.parsed is not None else {}
        return AIMessage(content=self.reply, additional_kwargs=kwargs)


def test_aligned_clean_verdict_not_blocked() -> None:
    v = OutputJudgeVerdict(aligned=True, leak_suspected=False, reason="ok")
    assert not v.blocked


def test_misaligned_verdict_blocked() -> None:
    v = OutputJudgeVerdict(aligned=False, leak_suspected=False, reason="off-task")
    assert v.blocked


def test_leak_suspected_blocks_even_when_aligned() -> None:
    v = OutputJudgeVerdict(aligned=True, leak_suspected=True, reason="leak")
    assert v.blocked


@pytest.mark.asyncio
async def test_fake_judge_returns_configured_verdict() -> None:
    v = OutputJudgeVerdict(aligned=False, leak_suspected=False, reason="x")
    judge = FakeOutputJudge(verdict=v)
    out = await judge.judge(user_request="q", response="r", context_hint=None)
    assert out is v


@pytest.mark.asyncio
async def test_fake_judge_raises_when_configured() -> None:
    judge = FakeOutputJudge(raises=True)
    with pytest.raises(RuntimeError):
        await judge.judge(user_request="q", response="r", context_hint=None)


# --- LLMOutputJudge (PI-2b-2) ----------------------------------------------


@pytest.mark.asyncio
async def test_llm_judge_parses_aligned_verdict() -> None:
    caller = _FakeCaller('{"aligned": true, "leak_suspected": false, "reason": "on-task"}')
    v = await LLMOutputJudge(caller=caller).judge(
        user_request="translate", response="Bonjour", context_hint=None
    )
    assert not v.blocked
    assert v.reason == "on-task"


@pytest.mark.asyncio
async def test_llm_judge_parses_misaligned_leak_verdict() -> None:
    caller = _FakeCaller('{"aligned": false, "leak_suspected": true, "reason": "echoed a token"}')
    v = await LLMOutputJudge(caller=caller).judge(
        user_request="summarise", response="CANARY-7F3A21", context_hint=None
    )
    assert v.blocked
    assert v.leak_suspected


@pytest.mark.asyncio
async def test_llm_judge_raises_on_unparseable_reply() -> None:
    caller = _FakeCaller("I think it's probably fine?")
    with pytest.raises(ValueError, match="JSON"):
        await LLMOutputJudge(caller=caller).judge(user_request="q", response="r", context_hint=None)


# --- RT-1 PR-2: structured output through the caller ------------------------


@pytest.mark.asyncio
async def test_llm_judge_passes_output_schema_to_caller() -> None:
    caller = _FakeCaller('{"aligned": true, "leak_suspected": false, "reason": "ok"}')
    await LLMOutputJudge(caller=caller).judge(user_request="q", response="r", context_hint=None)
    spec = caller.seen_schema
    assert spec is not None
    assert spec.name == "output_judge_verdict"
    # The decision booleans are required; ``reason`` is descriptive and
    # optional (a missing reason must never void a BLOCK verdict).
    assert set(spec.schema["required"]) == {"aligned", "leak_suspected"}
    assert spec.schema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_llm_judge_blocks_leak_verdict_without_reason() -> None:
    """Safety pin — pre-RT-1 ``data.get("reason", "")`` tolerated a
    missing reason. A BLOCK verdict without one must stay a BLOCK, not
    become a validation failure that fail-open would ALLOW."""
    caller = _FakeCaller("x", parsed={"aligned": False, "leak_suspected": True})
    v = await LLMOutputJudge(caller=caller).judge(
        user_request="summarise", response="CANARY-7F3A21", context_hint=None
    )
    assert v.blocked
    assert v.leak_suspected
    assert v.reason == ""


@pytest.mark.asyncio
async def test_llm_judge_consumes_router_parsed_dict() -> None:
    """The validated ``parsed`` dict wins over the raw text — prose in
    ``content`` (e.g. the tool_call path's block list flattened oddly)
    must not break the verdict."""
    caller = _FakeCaller(
        "prose around the verdict",
        parsed={"aligned": False, "leak_suspected": True, "reason": "echoed a token"},
    )
    v = await LLMOutputJudge(caller=caller).judge(
        user_request="summarise", response="CANARY-7F3A21", context_hint=None
    )
    assert v.blocked
    assert v.leak_suspected
    assert v.reason == "echoed a token"


@pytest.mark.asyncio
async def test_llm_judge_parses_fenced_text_without_parsed() -> None:
    """A caller without structured-output support (no ``parsed``) still
    works when the text is a fenced JSON verdict."""
    caller = _FakeCaller(
        '```json\n{"aligned": true, "leak_suspected": false, "reason": "on-task"}\n```'
    )
    v = await LLMOutputJudge(caller=caller).judge(
        user_request="translate", response="Bonjour", context_hint=None
    )
    assert not v.blocked


@pytest.mark.asyncio
async def test_llm_judge_raises_on_invalid_parsed_shape() -> None:
    caller = _FakeCaller("x", parsed={"aligned": True})  # leak_suspected missing
    with pytest.raises(ValueError, match="JSON"):
        await LLMOutputJudge(caller=caller).judge(user_request="q", response="r", context_hint=None)


@pytest.mark.asyncio
async def test_llm_judge_lets_validation_error_propagate() -> None:
    """RT-ADR-3 — the router's exhausted-retries error must reach the
    caller's judge-failure policy (fail-open / fail-closed), exactly
    like any other judge failure. The judge must not swallow it."""

    class _ExplodingCaller:
        async def __call__(
            self,
            *,
            messages: Sequence[BaseMessage],
            tools: Sequence[object],
            output_schema: StructuredOutputSpec | None = None,
        ) -> AIMessage:
            raise LLMOutputValidationError("still invalid after retries")

    with pytest.raises(LLMOutputValidationError):
        await LLMOutputJudge(caller=_ExplodingCaller()).judge(
            user_request="q", response="r", context_hint=None
        )


@pytest.mark.asyncio
async def test_llm_judge_includes_request_and_response_in_prompt() -> None:
    caller = _FakeCaller('{"aligned": true, "leak_suspected": false, "reason": "ok"}')
    await LLMOutputJudge(caller=caller).judge(
        user_request="summarise the ticket", response="THE-CANARY", context_hint="api key"
    )
    assert caller.seen is not None
    user_msg = str(caller.seen[-1].content)
    assert "summarise the ticket" in user_msg
    assert "THE-CANARY" in user_msg
    assert "api key" in user_msg  # context_hint surfaced


# --- ActionJudge (PI-3b) ---------------------------------------------------


def test_action_verdict_blocked_property() -> None:
    assert ActionVerdict(aligned=False, reason="x").blocked
    assert not ActionVerdict(aligned=True, reason="x").blocked


@pytest.mark.asyncio
async def test_fake_action_judge_returns_and_raises() -> None:
    v = ActionVerdict(aligned=False, reason="off-task")
    assert (
        await FakeActionJudge(verdict=v).judge_action(user_request="q", tool_name="t", tool_args={})
        is v
    )
    with pytest.raises(RuntimeError):
        await FakeActionJudge(raises=True).judge_action(
            user_request="q", tool_name="t", tool_args={}
        )


@pytest.mark.asyncio
async def test_llm_action_judge_parses_and_prompts() -> None:
    caller = _FakeCaller('{"aligned": false, "reason": "exfil"}')
    v = await LLMActionJudge(caller=caller).judge_action(
        user_request="summarise", tool_name="http_post", tool_args={"url": "https://evil/x"}
    )
    assert v.blocked
    assert caller.seen is not None
    user_msg = str(caller.seen[-1].content)
    assert "summarise" in user_msg
    assert "http_post" in user_msg


@pytest.mark.asyncio
async def test_llm_action_judge_raises_on_unparseable() -> None:
    with pytest.raises(ValueError, match="JSON"):
        await LLMActionJudge(caller=_FakeCaller("maybe?")).judge_action(
            user_request="q", tool_name="t", tool_args={}
        )


@pytest.mark.asyncio
async def test_llm_action_judge_passes_schema_and_consumes_parsed() -> None:
    caller = _FakeCaller("prose", parsed={"aligned": True, "reason": "on-task listing"})
    v = await LLMActionJudge(caller=caller).judge_action(
        user_request="list files", tool_name="list_dir", tool_args={"path": "."}
    )
    assert not v.blocked
    assert v.reason == "on-task listing"
    spec = caller.seen_schema
    assert spec is not None
    assert spec.name == "action_judge_verdict"
    assert set(spec.schema["required"]) == {"aligned"}


@pytest.mark.asyncio
async def test_llm_action_judge_blocks_misaligned_verdict_without_reason() -> None:
    """Same safety pin as the output judge — missing reason ≠ failure."""
    caller = _FakeCaller("x", parsed={"aligned": False})
    v = await LLMActionJudge(caller=caller).judge_action(
        user_request="summarise", tool_name="http_post", tool_args={"url": "https://evil/x"}
    )
    assert v.blocked
    assert v.reason == ""
