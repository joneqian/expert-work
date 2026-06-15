"""Unit tests for the PI-2b output-judge seam."""

from __future__ import annotations

import pytest

from orchestrator.output_judge import FakeOutputJudge, OutputJudgeVerdict


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
