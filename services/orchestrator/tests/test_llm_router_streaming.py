import asyncio
from collections.abc import AsyncIterator

import pytest

from expert_work.runtime.middleware import (
    LLMClientError,
    LLMServerError,
    LLMStreamInterruptedError,
)
from orchestrator.llm.providers._streaming import LLMDelta
from orchestrator.llm.router import AllProvidersExhaustedError, LLMRouter, ProviderHandle


class _StreamProvider:
    """Streaming double: a scripted list where an item is either an LLMDelta
    or a float (a sleep gap before the next delta) or an Exception (raised)."""

    def __init__(self, script: list) -> None:
        self.script = script

    async def stream(self, *, messages, tools, output_schema=None) -> AsyncIterator[LLMDelta]:
        for item in self.script:
            if isinstance(item, (int, float)):
                await asyncio.sleep(item)
            elif isinstance(item, Exception):
                raise item
            else:
                yield item


def _handle(script: list, key: str = "glm:glm-5.2") -> ProviderHandle:
    return ProviderHandle(provider=_StreamProvider(script), key=key)


@pytest.mark.asyncio
async def test_idle_fires_on_silence_not_on_slow_total() -> None:
    # First token is slow (0.05s < first_token 0.2s), then steady sub-idle
    # deltas that far outlast any single total cap — must NOT time out.
    script = [
        0.05,
        LLMDelta(content="a"),
        0.02,
        LLMDelta(content="b"),
        0.02,
        LLMDelta(content="c"),
        LLMDelta(finish_reason="stop"),
    ]
    router = LLMRouter(providers=[_handle(script)], first_token_timeout_s=0.2, idle_timeout_s=0.1)
    msg = await router(messages=[], tools=[])
    assert msg.content == "abc"


@pytest.mark.asyncio
async def test_first_token_timeout_falls_over_to_next_provider() -> None:
    slow = _handle([0.3, LLMDelta(content="never")], key="glm:a")  # stalls before first token
    good = _handle([LLMDelta(content="ok"), LLMDelta(finish_reason="stop")], key="glm:b")
    router = LLMRouter(providers=[slow, good], first_token_timeout_s=0.1, idle_timeout_s=0.5)
    msg = await router(messages=[], tools=[])
    assert msg.content == "ok"  # fell over to the second provider


@pytest.mark.asyncio
async def test_idle_after_first_token_ends_turn_with_partial() -> None:
    # First token arrives, then the stream stalls past idle_timeout.
    slow = _handle([LLMDelta(content="partial "), LLMDelta(content="answer"), 0.3], key="glm:a")
    never = _handle([LLMDelta(content="SHOULD NOT REACH")], key="glm:b")
    router = LLMRouter(providers=[slow, never], first_token_timeout_s=0.5, idle_timeout_s=0.1)
    msg = await router(messages=[], tools=[])
    assert msg.content == "partial answer"
    assert msg.response_metadata.get("finish_reason") == "stream_idle_timeout"


@pytest.mark.asyncio
async def test_in_band_error_before_first_token_is_retryable() -> None:
    err = _handle([LLMServerError("boom before token")], key="glm:a")
    good = _handle([LLMDelta(content="ok"), LLMDelta(finish_reason="stop")], key="glm:b")
    router = LLMRouter(providers=[err, good], first_token_timeout_s=0.5, idle_timeout_s=0.5)
    msg = await router(messages=[], tools=[])
    assert msg.content == "ok"  # server error pre-token → fell over


@pytest.mark.asyncio
async def test_error_after_first_token_is_terminal_no_fallback() -> None:
    err = _handle([LLMDelta(content="partial"), LLMServerError("mid-stream boom")], key="glm:a")
    good = _handle([LLMDelta(content="SHOULD NOT REACH")], key="glm:b")
    router = LLMRouter(providers=[err, good], first_token_timeout_s=0.5, idle_timeout_s=0.5)
    with pytest.raises(LLMStreamInterruptedError):
        await router(messages=[], tools=[])


@pytest.mark.asyncio
async def test_client_error_before_first_token_no_fallback() -> None:
    err = _handle([LLMClientError("400 malformed")], key="glm:a")
    good = _handle([LLMDelta(content="ok")], key="glm:b")
    router = LLMRouter(providers=[err, good], first_token_timeout_s=0.5, idle_timeout_s=0.5)
    with pytest.raises(LLMClientError):
        await router(messages=[], tools=[])


@pytest.mark.asyncio
async def test_first_token_timeout_all_exhausted() -> None:
    a = _handle([0.3, LLMDelta(content="x")], key="glm:a")
    b = _handle([0.3, LLMDelta(content="y")], key="glm:b")
    router = LLMRouter(providers=[a, b], first_token_timeout_s=0.05, idle_timeout_s=0.5)
    with pytest.raises(AllProvidersExhaustedError):
        await router(messages=[], tools=[])
