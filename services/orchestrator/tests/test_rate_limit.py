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

        def new_stream_assembler(self):
            from orchestrator.llm.providers._streaming import OpenAIStreamAssembler

            return OpenAIStreamAssembler()

    inner = _StreamingInner()
    limited = RateLimitedProvider(inner=inner, limiter=AsyncLimiter(max_rate=100, time_period=60))
    out = [d.content async for d in limited.stream(messages=["m"], tools=[])]
    assert out == ["a", "b"]
    assert inner.seen == [(["m"], [])]
    assert supports_streaming(limited) is True


@pytest.mark.asyncio
async def test_stream_forwards_output_schema() -> None:
    from collections.abc import AsyncIterator

    from aiolimiter import AsyncLimiter

    from expert_work.protocol import StructuredOutputSpec
    from orchestrator.llm.providers._streaming import LLMDelta
    from orchestrator.llm.rate_limit import RateLimitedProvider

    spec = StructuredOutputSpec(schema={"type": "object"}, name="x")

    class _CapturingInner:
        def __init__(self) -> None:
            self.captured: dict = {}

        async def stream(self, *, messages, tools, output_schema=None) -> AsyncIterator[LLMDelta]:
            self.captured["output_schema"] = output_schema
            yield LLMDelta(content="ok")

    inner = _CapturingInner()
    limited = RateLimitedProvider(inner=inner, limiter=AsyncLimiter(max_rate=100, time_period=60))
    out = [d async for d in limited.stream(messages=["m"], tools=[], output_schema=spec)]
    assert [d.content for d in out] == ["ok"]
    assert inner.captured["output_schema"] is spec


@pytest.mark.asyncio
async def test_stream_none_schema_keeps_legacy_inner() -> None:
    from collections.abc import AsyncIterator

    from aiolimiter import AsyncLimiter

    from orchestrator.llm.providers._streaming import LLMDelta
    from orchestrator.llm.rate_limit import RateLimitedProvider

    class _LegacyStreamingInner:
        # No output_schema parameter — proves the None branch OMITS the kwarg
        # (if the code passed output_schema=None here, this would TypeError).
        async def stream(self, *, messages, tools) -> AsyncIterator[LLMDelta]:
            yield LLMDelta(content="ok")

    limited = RateLimitedProvider(
        inner=_LegacyStreamingInner(), limiter=AsyncLimiter(max_rate=100, time_period=60)
    )
    out = [d async for d in limited.stream(messages=["m"], tools=[])]  # output_schema defaults None
    assert [d.content for d in out] == ["ok"]
