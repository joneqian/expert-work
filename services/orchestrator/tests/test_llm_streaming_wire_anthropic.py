from orchestrator.llm.providers._streaming import (
    AnthropicStreamAssembler,
    ToolCallChunk,
    delta_from_anthropic_event,
)
from orchestrator.llm.providers.anthropic import _from_anthropic_response


def test_text_delta_is_progress() -> None:
    d = delta_from_anthropic_event(
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}}
    )
    assert d.content == "Hel"
    assert d.has_progress is True


def test_thinking_delta_is_reasoning_progress() -> None:
    d = delta_from_anthropic_event(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "hmm"},
        }
    )
    assert d.reasoning == "hmm"
    assert d.has_progress is True


def test_message_start_usage_no_progress() -> None:
    d = delta_from_anthropic_event(
        {
            "type": "message_start",
            "message": {
                "model": "claude-x",
                "usage": {"input_tokens": 10, "cache_read_input_tokens": 4},
            },
        }
    )
    assert d.has_progress is False
    assert d.usage == {"input_tokens": 10, "cache_read_input_tokens": 4}
    assert d.model == "claude-x"


def test_tool_use_start_and_json_delta() -> None:
    start = delta_from_anthropic_event(
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "toolu_1", "name": "search"},
        }
    )
    assert start.tool_calls == (ToolCallChunk(index=1, id="toolu_1", name="search"),)
    frag = delta_from_anthropic_event(
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"q":'},
        }
    )
    assert frag.tool_calls == (ToolCallChunk(index=1, args_fragment='{"q":'),)


def test_message_delta_finish_and_output_usage() -> None:
    d = delta_from_anthropic_event(
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 7},
        }
    )
    assert d.finish_reason == "end_turn"
    assert d.usage == {"output_tokens": 7}


def test_assembler_text_matches_decoder() -> None:
    body = {
        "content": [{"type": "text", "text": "Hello world"}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    expected = _from_anthropic_response(body)
    asm = AnthropicStreamAssembler()
    asm.add(
        delta_from_anthropic_event(
            {
                "type": "message_start",
                "message": {"model": "claude-x", "usage": {"input_tokens": 10}},
            }
        )
    )
    asm.add(
        delta_from_anthropic_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hello "},
            }
        )
    )
    asm.add(
        delta_from_anthropic_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "world"},
            }
        )
    )
    asm.add(
        delta_from_anthropic_event(
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 5},
            }
        )
    )
    got = asm.build()
    assert got.content == expected.content
    assert got.usage_metadata == expected.usage_metadata
    assert got.tool_calls == expected.tool_calls


def test_assembler_reassembles_tool_use() -> None:
    body = {
        "content": [{"type": "tool_use", "id": "toolu_1", "name": "search", "input": {"q": "hi"}}],
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }
    expected = _from_anthropic_response(body)
    asm = AnthropicStreamAssembler()
    asm.add(
        delta_from_anthropic_event(
            {"type": "message_start", "message": {"usage": {"input_tokens": 3}}}
        )
    )
    asm.add(
        delta_from_anthropic_event(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_1", "name": "search"},
            }
        )
    )
    asm.add(
        delta_from_anthropic_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"q": '},
            }
        )
    )
    asm.add(
        delta_from_anthropic_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '"hi"}'},
            }
        )
    )
    asm.add(
        delta_from_anthropic_event(
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 2},
            }
        )
    )
    got = asm.build()
    assert got.tool_calls == expected.tool_calls


def test_assembler_interrupted_drops_incomplete_tool() -> None:
    asm = AnthropicStreamAssembler()
    asm.add(
        delta_from_anthropic_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "partial"},
            }
        )
    )
    asm.add(
        delta_from_anthropic_event(
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "id": "toolu_1", "name": "search"},
            }
        )
    )
    asm.add(
        delta_from_anthropic_event(
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"q": '},
            }
        )
    )
    got = asm.build(interrupted=True)
    assert got.content == "partial"
    assert got.tool_calls == []


def test_assembler_thinking_dropped_from_final() -> None:
    # thinking is progress (resets idle) but NOT part of the final message
    # (the decoder ignores non-text/tool_use blocks).
    asm = AnthropicStreamAssembler()
    asm.add(
        delta_from_anthropic_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "reasoning..."},
            }
        )
    )
    asm.add(
        delta_from_anthropic_event(
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "answer"},
            }
        )
    )
    got = asm.build()
    assert got.content == "answer"
    assert "reasoning" not in str(got.additional_kwargs)
