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
            if isinstance(item, int | float):
                await asyncio.sleep(item)
            elif isinstance(item, Exception):
                raise item
            else:
                yield item

    def new_stream_assembler(self):
        from orchestrator.llm.providers._streaming import OpenAIStreamAssembler

        return OpenAIStreamAssembler()


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
async def test_delta_sink_reset_after_exception() -> None:
    from orchestrator.llm import router as router_mod

    err = _handle([LLMClientError("400")], key="glm:a")
    router = LLMRouter(providers=[err], first_token_timeout_s=0.5, idle_timeout_s=0.5)

    seen: list = []

    async def on_delta(d: LLMDelta) -> None:
        seen.append(d)

    with pytest.raises(LLMClientError):
        await router(messages=[], tools=[], on_delta=on_delta)

    assert router_mod._delta_sink.get() is None


@pytest.mark.asyncio
async def test_first_token_timeout_all_exhausted() -> None:
    a = _handle([0.3, LLMDelta(content="x")], key="glm:a")
    b = _handle([0.3, LLMDelta(content="y")], key="glm:b")
    router = LLMRouter(providers=[a, b], first_token_timeout_s=0.05, idle_timeout_s=0.5)
    with pytest.raises(AllProvidersExhaustedError):
        await router(messages=[], tools=[])


@pytest.mark.asyncio
async def test_structured_output_uses_non_streaming_path() -> None:
    # output_schema set -> router must NOT drive the stream; it calls the
    # provider's complete() path (structured output does not stream).
    from langchain_core.messages import AIMessage

    from expert_work.protocol import StructuredOutputSpec

    class _Probe:
        def __init__(self) -> None:
            self.stream_calls = 0
            self.complete_calls = 0

        async def stream(self, *, messages, tools, output_schema=None):
            self.stream_calls += 1
            yield LLMDelta(content="SHOULD NOT STREAM")

        async def complete(self, *, messages, tools, output_schema=None) -> AIMessage:
            self.complete_calls += 1
            return AIMessage(content='{"ok": true}', additional_kwargs={"parsed": {"ok": True}})

        def new_stream_assembler(self):
            from orchestrator.llm.providers._streaming import OpenAIStreamAssembler

            return OpenAIStreamAssembler()

    probe = _Probe()
    router = LLMRouter(
        providers=[ProviderHandle(provider=probe, key="x")],
        first_token_timeout_s=0.5,
        idle_timeout_s=0.5,
    )
    spec = StructuredOutputSpec(schema={"type": "object"}, name="x")
    msg = await router(messages=[], tools=[], output_schema=spec)
    assert probe.stream_calls == 0
    assert probe.complete_calls == 1
    assert msg.additional_kwargs["parsed"] == {"ok": True}


@pytest.mark.asyncio
async def test_drive_stream_uses_provider_assembler() -> None:
    # A streaming provider that supplies its own assembler must have THAT
    # assembler used by the router (not a hard-coded OpenAI one).
    from orchestrator.llm.providers._streaming import OpenAIStreamAssembler

    used = {}

    class _MarkAssembler(OpenAIStreamAssembler):
        def build(self, *, interrupted: bool = False):
            used["hit"] = True
            return super().build(interrupted=interrupted)

    class _P:
        async def stream(self, *, messages, tools, output_schema=None):
            yield LLMDelta(content="ok")
            yield LLMDelta(finish_reason="stop")

        def new_stream_assembler(self):
            return _MarkAssembler()

    router = LLMRouter(
        providers=[ProviderHandle(provider=_P(), key="x")],
        first_token_timeout_s=0.5,
        idle_timeout_s=0.5,
    )
    msg = await router(messages=[], tools=[])
    assert msg.content == "ok"
    assert used.get("hit") is True


@pytest.mark.asyncio
async def test_on_delta_awaited_for_each_delta_on_streaming_path() -> None:
    script = [LLMDelta(content="a"), LLMDelta(content="b"), LLMDelta(finish_reason="stop")]
    router = LLMRouter(providers=[_handle(script)], first_token_timeout_s=0.5, idle_timeout_s=0.5)
    seen: list[str] = []

    async def on_delta(d: LLMDelta) -> None:
        seen.append(d.content)

    msg = await router(messages=[], tools=[], on_delta=on_delta)
    assert msg.content == "ab"
    assert seen == ["a", "b", ""]  # every delta, incl. the empty-content finish delta


@pytest.mark.asyncio
async def test_on_delta_not_called_on_structured_path() -> None:
    from langchain_core.messages import AIMessage

    from expert_work.protocol import StructuredOutputSpec

    class _Probe:
        async def stream(self, *, messages, tools, output_schema=None):
            yield LLMDelta(content="SHOULD NOT STREAM")

        async def complete(self, *, messages, tools, output_schema=None) -> AIMessage:
            return AIMessage(content='{"ok": true}', additional_kwargs={"parsed": {"ok": True}})

        def new_stream_assembler(self):
            from orchestrator.llm.providers._streaming import OpenAIStreamAssembler

            return OpenAIStreamAssembler()

    seen: list = []

    async def on_delta(d: LLMDelta) -> None:
        seen.append(d)

    router = LLMRouter(
        providers=[ProviderHandle(provider=_Probe(), key="x")],
        first_token_timeout_s=0.5,
        idle_timeout_s=0.5,
    )
    spec = StructuredOutputSpec(schema={"type": "object"}, name="x")
    await router(messages=[], tools=[], output_schema=spec, on_delta=on_delta)
    assert seen == []
