"""Tests for rate-limited LLM provider."""

import pytest


@pytest.mark.asyncio
async def test_stream_delegates_and_admits() -> None:
    from collections.abc import AsyncIterator

    from aiolimiter import AsyncLimiter

    from orchestrator.llm.providers._streaming import LLMDelta, supports_streaming
    from orchestrator.llm.rate_limit import RateLimitedProvider

    class _StreamingInner:
        def __init__(self) -> None:
            self.seen: list = []

        async def stream(self, *, messages, tools, output_schema=None) -> AsyncIterator[LLMDelta]:
            self.seen.append((list(messages), list(tools)))
            yield LLMDelta(content="a")
            yield LLMDelta(content="b")

    inner = _StreamingInner()
    limited = RateLimitedProvider(inner=inner, limiter=AsyncLimiter(max_rate=100, time_period=60))
    out = [d.content async for d in limited.stream(messages=["m"], tools=[])]
    assert out == ["a", "b"]
    assert inner.seen == [(["m"], [])]
    assert supports_streaming(limited) is True
