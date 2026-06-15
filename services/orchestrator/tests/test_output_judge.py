"""Unit tests for the PI-2b output-judge seam + LLM judge."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from orchestrator.output_judge import (
    FakeOutputJudge,
    LLMOutputJudge,
    OutputJudgeVerdict,
)


@dataclass
class _FakeCaller:
    """Returns a canned reply; records the last messages it was called with."""

    reply: str
    seen: list[BaseMessage] | None = None

    async def __call__(
        self, *, messages: Sequence[BaseMessage], tools: Sequence[object]
    ) -> AIMessage:
        del tools
        self.seen = list(messages)
        return AIMessage(content=self.reply)


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
    caller = _FakeCaller(
        'Here: {"aligned": false, "leak_suspected": true, "reason": "echoed a token"}'
    )
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
