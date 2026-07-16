from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from expert_work.runtime.middleware import LLMClientError
from orchestrator.llm.providers._streaming import OpenAIStreamAssembler
from orchestrator.llm.providers.openai import OpenAIProvider, RecordingOpenAIClient
from orchestrator.tools.registry import ToolSpec


def _text_chunks() -> list[dict]:
    return [
        {"choices": [{"delta": {"role": "assistant"}}]},
        {"choices": [{"delta": {"content": "Hel"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
        {
            "choices": [{"delta": {}}],
            "model": "glm-5.2",
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
    ]


@pytest.mark.asyncio
async def test_stream_yields_normalized_deltas() -> None:
    client = RecordingOpenAIClient(stream_chunks=_text_chunks())
    provider = OpenAIProvider(client=client, model="glm-5.2")
    deltas = [d async for d in provider.stream(messages=[HumanMessage(content="hi")], tools=[])]
    assert [d.content for d in deltas if d.content] == ["Hel", "lo"]
    assert any(d.usage and d.usage["total_tokens"] == 5 for d in deltas)
    assert client.calls[-1]["stream"] is True  # stream() went through the streaming client method


@pytest.mark.asyncio
async def test_stream_then_assemble_equals_non_streaming_complete() -> None:
    # The end-to-end byte-equality pin: the SAME logical response, delivered
    # once as a whole body (complete) and once as chunks (stream+assemble),
    # yields an identical AIMessage.
    whole = {
        "choices": [
            {"message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}
        ],
        "model": "glm-5.2",
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }
    complete_provider = OpenAIProvider(
        client=RecordingOpenAIClient(response=whole), model="glm-5.2"
    )
    expected = await complete_provider.complete(messages=[HumanMessage(content="hi")], tools=[])

    stream_provider = OpenAIProvider(
        client=RecordingOpenAIClient(stream_chunks=_text_chunks()), model="glm-5.2"
    )
    asm = OpenAIStreamAssembler()
    async for d in stream_provider.stream(messages=[HumanMessage(content="hi")], tools=[]):
        asm.add(d)
    got = asm.build()

    assert got.content == expected.content
    assert got.additional_kwargs == expected.additional_kwargs
    assert got.response_metadata == expected.response_metadata
    assert got.usage_metadata == expected.usage_metadata
    assert got.tool_calls == expected.tool_calls


@pytest.mark.asyncio
async def test_stream_reassembles_tool_call_fragments() -> None:
    client = RecordingOpenAIClient(
        stream_chunks=[
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "function": {"name": "search", "arguments": '{"q": '},
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"hi"}'}}]},
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        ]
    )
    provider = OpenAIProvider(client=client, model="glm-5.2")
    asm = OpenAIStreamAssembler()
    async for d in provider.stream(messages=[HumanMessage(content="hi")], tools=[]):
        asm.add(d)
    msg = asm.build()
    expected_tool_call = {
        "id": "call_1",
        "name": "search",
        "args": {"q": "hi"},
        "type": "tool_call",
    }
    assert msg.tool_calls == [expected_tool_call]


@pytest.mark.asyncio
async def test_stream_allowed_tools_rejection_falls_back_and_sticks() -> None:
    """HX-J4 — a 4xx with the allowed_tools constraint re-streams once without
    it and the provider instance stays on the application tier afterwards."""

    @dataclass
    class _RejectConstraintStream:
        calls: list[dict[str, Any]] = field(default_factory=list)

        async def stream_chat_completions(self, **kwargs: Any) -> AsyncIterator[Mapping[str, Any]]:
            self.calls.append(kwargs)
            if kwargs.get("tool_choice") is not None:
                raise LLMClientError("openai 400: unknown tool_choice type")
            for chunk in [{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}]:
                yield chunk

    client = _RejectConstraintStream()
    provider = OpenAIProvider(client=client, model="gpt-5.5")
    deferred_tools = [ToolSpec(name="mcp:gh.issue", description="d", defer_loading=True)]

    out = [
        d
        async for d in provider.stream(messages=[HumanMessage(content="hi")], tools=deferred_tools)
    ]
    assert "".join(d.content for d in out) == "ok"
    assert client.calls[0]["tool_choice"] is not None  # constrained first attempt
    assert client.calls[1]["tool_choice"] is None  # unconstrained retry

    # Sticky fallback: next stream goes straight out unconstrained.
    out2 = [
        d
        async for d in provider.stream(messages=[HumanMessage(content="hi")], tools=deferred_tools)
    ]
    assert "".join(d.content for d in out2) == "ok"
    assert client.calls[2]["tool_choice"] is None


@pytest.mark.asyncio
async def test_stream_plain_client_error_propagates_without_fallback() -> None:
    client = RecordingOpenAIClient(raise_with=LLMClientError("openai 400: bad request"))
    provider = OpenAIProvider(client=client, model="gpt-5.5")
    with pytest.raises(LLMClientError):
        _ = [
            d
            async for d in provider.stream(
                messages=[HumanMessage(content="hi")],
                tools=[ToolSpec(name="active_tool", description="x")],
            )
        ]
    assert len(client.calls) == 1  # no retry (use_allowed False → plain error propagates)
