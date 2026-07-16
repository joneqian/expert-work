from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage

from orchestrator.llm.providers._streaming import (
    LLMDelta,
    OpenAIStreamAssembler,
    StreamingLLMProvider,
    ToolCallChunk,
    delta_from_openai_chunk,
    supports_streaming,
)
from orchestrator.llm.providers.openai import _from_openai_response


def _chunk(delta: dict[str, Any], *, finish: str | None = None, **top: Any) -> dict[str, Any]:
    return {"choices": [{"delta": delta, "finish_reason": finish}], **top}


def test_delta_content_and_progress() -> None:
    d = delta_from_openai_chunk(_chunk({"content": "Hel"}))
    assert d.content == "Hel"
    assert d.has_progress is True


def test_delta_role_only_is_not_progress() -> None:
    d = delta_from_openai_chunk(_chunk({"role": "assistant"}))
    assert d.content == ""
    assert d.reasoning == ""
    assert d.tool_calls == ()
    assert d.has_progress is False


def test_delta_reasoning_is_progress() -> None:
    d = delta_from_openai_chunk(_chunk({"reasoning_content": "thinking"}))
    assert d.reasoning == "thinking"
    assert d.has_progress is True


def test_delta_tool_call_fragment() -> None:
    d = delta_from_openai_chunk(
        _chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "function": {"name": "search", "arguments": '{"q":'},
                    }
                ]
            }
        )
    )
    expected_chunk = ToolCallChunk(index=0, id="call_1", name="search", args_fragment='{"q":')
    assert d.tool_calls == (expected_chunk,)
    assert d.has_progress is True


def test_delta_final_chunk_usage_and_finish() -> None:
    d = delta_from_openai_chunk(
        _chunk(
            {},
            finish="stop",
            usage={"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
            model="glm-5.2",
            system_fingerprint="fp_1",
        )
    )
    assert d.finish_reason == "stop"
    assert d.usage == {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}
    assert d.model == "glm-5.2"
    assert d.system_fingerprint == "fp_1"
    assert d.has_progress is False


def test_assembler_text_matches_non_streaming_decoder() -> None:
    # The regression guarantee: the same content assembled from deltas must
    # byte-equal the AIMessage the non-streaming decoder produces.
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Hello world",
                    "reasoning_content": "let me think",
                },
                "finish_reason": "stop",
            }
        ],
        "model": "glm-5.2",
        "system_fingerprint": "fp_1",
        "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
    }
    expected = _from_openai_response(body)

    asm = OpenAIStreamAssembler()
    asm.add(delta_from_openai_chunk(_chunk({"role": "assistant"})))
    asm.add(delta_from_openai_chunk(_chunk({"reasoning_content": "let me think"})))
    asm.add(delta_from_openai_chunk(_chunk({"content": "Hello "})))
    asm.add(delta_from_openai_chunk(_chunk({"content": "world"})))
    asm.add(
        delta_from_openai_chunk(
            _chunk(
                {},
                finish="stop",
                model="glm-5.2",
                system_fingerprint="fp_1",
                usage={"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
            )
        )
    )
    got = asm.build()

    assert got.content == expected.content
    assert got.additional_kwargs == expected.additional_kwargs
    assert got.response_metadata == expected.response_metadata
    assert got.usage_metadata == expected.usage_metadata
    assert got.tool_calls == expected.tool_calls


def test_assembler_reassembles_tool_call_fragments() -> None:
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "search", "arguments": '{"q": "hi"}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    expected = _from_openai_response(body)

    asm = OpenAIStreamAssembler()
    asm.add(
        delta_from_openai_chunk(
            _chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "function": {"name": "search", "arguments": '{"q": '},
                        }
                    ]
                }
            )
        )
    )
    asm.add(
        delta_from_openai_chunk(
            _chunk(
                {"tool_calls": [{"index": 0, "function": {"arguments": '"hi"}'}}]},
                finish="tool_calls",
            )
        )
    )
    got = asm.build()
    assert got.tool_calls == expected.tool_calls


def test_assembler_interrupted_drops_incomplete_tool_call() -> None:
    # A tool-args fragment that never completed valid JSON must not become a
    # dispatchable tool call when the stream is interrupted mid-args.
    asm = OpenAIStreamAssembler()
    asm.add(delta_from_openai_chunk(_chunk({"content": "partial answer"})))
    asm.add(
        delta_from_openai_chunk(
            _chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "function": {"name": "search", "arguments": '{"q": '},
                        }
                    ]
                }
            )
        )
    )
    got = asm.build(interrupted=True)
    assert got.content == "partial answer"
    assert got.tool_calls == []
    assert got.response_metadata.get("finish_reason") == "stream_idle_timeout"


def test_supports_streaming_true_for_streaming_provider() -> None:
    class _Streamer:
        async def stream(self, **_: Any) -> AsyncIterator[LLMDelta]:
            if False:
                yield LLMDelta()

    class _Wrapper:  # mimics RateLimitedProvider.inner unwrapping
        def __init__(self, inner: Any) -> None:
            self.inner = inner

    assert isinstance(_Streamer(), StreamingLLMProvider)
    assert supports_streaming(_Streamer()) is True
    assert supports_streaming(_Wrapper(_Streamer())) is True


def test_supports_streaming_false_for_plain_provider() -> None:
    class _Plain:
        async def complete(self, **_: Any) -> AIMessage:
            return AIMessage(content="")

    assert supports_streaming(_Plain()) is False


def test_supports_streaming_safe_on_self_referential_inner() -> None:
    class _SelfRef:
        def __init__(self) -> None:
            self.inner: Any = self  # points at itself

    assert supports_streaming(_SelfRef()) is False  # terminates, no hang, no stream()


def test_delta_missing_choices_is_empty_no_progress() -> None:
    d = delta_from_openai_chunk({})
    assert d.content == ""
    assert d.reasoning == ""
    assert d.tool_calls == ()
    assert d.finish_reason is None
    assert d.has_progress is False
