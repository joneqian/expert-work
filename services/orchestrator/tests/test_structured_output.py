"""Unit tests for structured output enforcement — Stream RT-1 PR-1.

Covers STREAM-RT-DESIGN § 7 (RT-ADR-1 / RT-ADR-2):

- three enforcement paths (native / tool_call / prompt) — request shape
  per adapter + success end-to-end through the router;
- validate + retry loop in ``LLMRouter._attempt_call`` — bad JSON gets a
  correction message and retries on the SAME handle, two failed retries
  raise :class:`LLMOutputValidationError`;
- ``output_schema=None`` is wire-identical to pre-RT-1 behaviour
  (backward-compat hard constraint);
- validation failure NEVER rotates keys / fails over (RT-ADR-1);
- anthropic tool_call path forces a single schema tool and the HX-13
  allowed_tools / tool-search beta steps aside (§ 7.5).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from expert_work.protocol import StructuredOutputSpec
from expert_work.runtime.middleware import LLMOutputValidationError
from orchestrator.llm import (
    AnthropicProvider,
    LLMRouter,
    OpenAICompatibleProvider,
    OpenAIProvider,
    ProviderHandle,
    RateLimitedProvider,
    RecordingAnthropicClient,
    RecordingOpenAIClient,
)
from orchestrator.llm.router import _KEY_LEVEL_ERRORS
from orchestrator.llm.structured_output import (
    compact_schema,
    correction_message,
    schema_instruction,
    strip_json_fences,
    validate_structured_output,
)
from orchestrator.tools.registry import ToolSpec

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"score": {"type": "integer"}},
    "required": ["score"],
    "additionalProperties": False,
}

_SPEC = StructuredOutputSpec(schema=_SCHEMA, name="verdict")


@dataclass
class _StructuredProvider:
    """RT-1-aware LLMProvider stub — returns scripted responses in order.

    The last response is sticky so a 3-attempt loop against a 2-item
    script keeps returning the final item.
    """

    responses: list[AIMessage] = field(default_factory=list)
    calls: list[dict[str, object]] = field(default_factory=list)

    async def complete(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
        output_schema: StructuredOutputSpec | None = None,
    ) -> AIMessage:
        self.calls.append(
            {
                "messages": list(messages),
                "tools": list(tools),
                "output_schema": output_schema,
            }
        )
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


@dataclass
class _LegacyProvider:
    """Pre-RT-1 LLMProvider double — ``complete`` has NO output_schema
    parameter. The router must keep working with it when no schema is
    requested (backward-compat hard constraint)."""

    response: AIMessage
    calls: list[dict[str, object]] = field(default_factory=list)

    async def complete(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
    ) -> AIMessage:
        self.calls.append({"messages": list(messages), "tools": list(tools)})
        return self.response


def _handle(provider: object, key: str = "test:primary") -> ProviderHandle:
    return ProviderHandle(provider=provider, key=key)  # type: ignore[arg-type]


def _msgs() -> list[BaseMessage]:
    return [HumanMessage(content="rate this")]


# ---------------------------------------------------------------------------
# StructuredOutputSpec — protocol type
# ---------------------------------------------------------------------------


def test_spec_defaults_and_frozen() -> None:
    spec = StructuredOutputSpec(schema={"type": "object"}, name="x")
    assert spec.strict is True
    with pytest.raises(Exception):  # noqa: B017 - frozen dataclass raises FrozenInstanceError
        spec.name = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Helpers — fence stripping / validation / schema compaction
# ---------------------------------------------------------------------------


def test_strip_json_fences_variants() -> None:
    assert strip_json_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_json_fences('```\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_json_fences('  {"a": 1}  ') == '{"a": 1}'
    assert strip_json_fences('{"a": 1}') == '{"a": 1}'


def test_validate_structured_output_success_and_failures() -> None:
    ok, err = validate_structured_output(AIMessage(content='{"score": 5}'), _SPEC)
    assert ok == {"score": 5}
    assert err is None

    ok, err = validate_structured_output(AIMessage(content="not json"), _SPEC)
    assert ok is None
    assert err is not None and "JSON" in err

    ok, err = validate_structured_output(AIMessage(content="[1, 2]"), _SPEC)
    assert ok is None
    assert err is not None and "object" in err

    ok, err = validate_structured_output(AIMessage(content='{"score": "high"}'), _SPEC)
    assert ok is None
    assert err is not None and "score" in err


def test_compact_schema_recurses_dependent_schemas() -> None:
    schema = {
        "type": "object",
        "dependentSchemas": {
            "credit_card": {
                "description": "billing address required with a card",
                "required": ["billing_address"],
            },
        },
    }
    compacted = compact_schema(schema)
    assert compacted["dependentSchemas"]["credit_card"] == {"required": ["billing_address"]}


def test_compact_schema_recurses_draft07_dependencies_both_forms() -> None:
    schema = {
        "type": "object",
        "dependencies": {
            "a": ["b", "c"],
            "credit_card": {"description": "schema-form dependency", "type": "object"},
        },
    }
    compacted = compact_schema(schema)
    # Property-name-list form passes through untouched; schema form is
    # recursed with the annotation stripped.
    assert compacted["dependencies"]["a"] == ["b", "c"]
    assert compacted["dependencies"]["credit_card"] == {"type": "object"}


def test_compact_schema_drops_descriptions_keeps_description_property() -> None:
    schema = {
        "type": "object",
        "description": "outer doc",
        "properties": {
            "description": {"type": "string", "description": "inner doc"},
            "nested": {
                "type": "array",
                "items": {"type": "object", "description": "item doc"},
            },
        },
    }
    compacted = compact_schema(schema)
    assert "description" not in compacted
    # The property literally NAMED "description" survives; only the
    # keyword usages are stripped.
    assert compacted["properties"]["description"] == {"type": "string"}
    assert compacted["properties"]["nested"]["items"] == {"type": "object"}


# ---------------------------------------------------------------------------
# RT-ADR-1 — router validation loop (chain=None unit path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_response_lands_in_parsed() -> None:
    provider = _StructuredProvider(responses=[AIMessage(content='{"score": 4}')])
    router = LLMRouter(providers=[_handle(provider)])

    result = await router(messages=_msgs(), tools=[], output_schema=_SPEC)

    assert result.additional_kwargs["parsed"] == {"score": 4}
    assert len(provider.calls) == 1
    assert provider.calls[0]["output_schema"] is _SPEC


@pytest.mark.asyncio
async def test_fenced_response_parses() -> None:
    """Prompt-path responses arrive wrapped in ```json fences — the
    validation loop strips them before parsing."""
    provider = _StructuredProvider(responses=[AIMessage(content='```json\n{"score": 2}\n```')])
    router = LLMRouter(providers=[_handle(provider)])

    result = await router(messages=_msgs(), tools=[], output_schema=_SPEC)

    assert result.additional_kwargs["parsed"] == {"score": 2}


@pytest.mark.asyncio
async def test_bad_json_retries_with_correction_and_recovers() -> None:
    provider = _StructuredProvider(
        responses=[
            AIMessage(content="sure! here you go"),
            AIMessage(content='{"score": 3}'),
        ]
    )
    router = LLMRouter(providers=[_handle(provider)])

    result = await router(messages=_msgs(), tools=[], output_schema=_SPEC)

    assert result.additional_kwargs["parsed"] == {"score": 3}
    assert len(provider.calls) == 2
    # The retry rides the SAME handle with the invalid response + a
    # correction user message appended.
    retry_messages = provider.calls[1]["messages"]
    assert isinstance(retry_messages, list)
    assert isinstance(retry_messages[-2], AIMessage)
    assert retry_messages[-2].content == "sure! here you go"
    assert isinstance(retry_messages[-1], HumanMessage)
    assert "JSON" in str(retry_messages[-1].content)


@pytest.mark.asyncio
async def test_schema_violation_retries_with_error_summary() -> None:
    provider = _StructuredProvider(
        responses=[
            AIMessage(content='{"score": "high"}'),
            AIMessage(content='{"score": 5}'),
        ]
    )
    router = LLMRouter(providers=[_handle(provider)])

    result = await router(messages=_msgs(), tools=[], output_schema=_SPEC)

    assert result.additional_kwargs["parsed"] == {"score": 5}
    correction = provider.calls[1]["messages"][-1]  # type: ignore[index]
    assert isinstance(correction, HumanMessage)
    assert "score" in str(correction.content)


@pytest.mark.asyncio
async def test_two_failed_retries_raise_output_validation_error() -> None:
    provider = _StructuredProvider(responses=[AIMessage(content="never json")])
    router = LLMRouter(providers=[_handle(provider)])

    with pytest.raises(LLMOutputValidationError, match="verdict"):
        await router(messages=_msgs(), tools=[], output_schema=_SPEC)

    # Initial attempt + exactly MAX_VALIDATION_RETRIES (2) resends.
    assert len(provider.calls) == 3


@pytest.mark.asyncio
async def test_validation_failure_never_rotates_key_or_fails_over() -> None:
    """RT-ADR-1 — a schema-validation failure is model behaviour, not a
    key/provider fault: the second handle must never be tried."""
    bad = _StructuredProvider(responses=[AIMessage(content="not json")])
    sibling = _StructuredProvider(responses=[AIMessage(content='{"score": 1}')])
    fallback = _StructuredProvider(responses=[AIMessage(content='{"score": 1}')])
    router = LLMRouter(
        providers=[
            ProviderHandle(provider=bad, key="p:m#1", group="p:m"),  # type: ignore[arg-type]
            ProviderHandle(provider=sibling, key="p:m#2", group="p:m"),  # type: ignore[arg-type]
            ProviderHandle(provider=fallback, key="q:m#1", group="q:m"),  # type: ignore[arg-type]
        ]
    )

    with pytest.raises(LLMOutputValidationError):
        await router(messages=_msgs(), tools=[], output_schema=_SPEC)

    assert len(bad.calls) == 3  # retried in place only
    assert sibling.calls == []
    assert fallback.calls == []


def test_output_validation_error_not_key_level() -> None:
    """The error class must never join the key-rotation family."""
    assert LLMOutputValidationError not in _KEY_LEVEL_ERRORS
    assert not any(issubclass(LLMOutputValidationError, err) for err in _KEY_LEVEL_ERRORS)


@pytest.mark.asyncio
async def test_validation_retry_and_failure_emit_counters() -> None:
    """RT-1 cost observability — resends and terminal failures are counted
    per provider key."""
    from prometheus_client import REGISTRY

    retry_metric = "expert_work_llm_structured_validation_retry_total"
    failure_metric = "expert_work_llm_structured_validation_failure_total"
    labels = {"provider_key": "structured-counter-test"}
    retry_before = REGISTRY.get_sample_value(retry_metric, labels=labels) or 0.0
    failure_before = REGISTRY.get_sample_value(failure_metric, labels=labels) or 0.0

    provider = _StructuredProvider(responses=[AIMessage(content="never json")])
    router = LLMRouter(providers=[_handle(provider, "structured-counter-test")])
    with pytest.raises(LLMOutputValidationError):
        await router(messages=_msgs(), tools=[], output_schema=_SPEC)

    retry_after = REGISTRY.get_sample_value(retry_metric, labels=labels) or 0.0
    failure_after = REGISTRY.get_sample_value(failure_metric, labels=labels) or 0.0
    assert retry_after == retry_before + 2  # two resends
    assert failure_after == failure_before + 1  # one terminal failure


@pytest.mark.asyncio
async def test_chain_payload_output_schema_is_json_serializable() -> None:
    """The middleware payload carries a plain summary dict (no dataclass,
    no schema body) so observability middlewares can json.dumps it."""
    from expert_work.runtime.middleware import MiddlewareChain, MiddlewareContext

    seen: list[dict[str, object]] = []

    class _Capture:
        name = "capture"
        anchor = "around_llm_call"
        after: tuple[str, ...] = ()
        before: tuple[str, ...] = ()

        async def __call__(self, ctx: MiddlewareContext, call_next: object) -> None:
            seen.append(dict(ctx.payload))
            await call_next(ctx)  # type: ignore[operator]

    provider = _StructuredProvider(responses=[AIMessage(content='{"score": 1}')])
    router = LLMRouter(
        providers=[_handle(provider)],
        around_llm_chain=MiddlewareChain("around_llm_call", (_Capture(),)),
    )

    result = await router(messages=_msgs(), tools=[], output_schema=_SPEC)

    assert result.additional_kwargs["parsed"] == {"score": 1}
    assert seen[0]["output_schema"] == {"name": "verdict", "strict": True}
    payload_schema = seen[0]["output_schema"]
    serialized = json.dumps(payload_schema)  # must not raise
    assert "schema" not in json.loads(serialized)  # no schema body in payload


# ---------------------------------------------------------------------------
# Backward compat — output_schema=None is wire-identical
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_none_schema_works_with_legacy_provider_double() -> None:
    """A pre-RT-1 double whose ``complete`` lacks the output_schema
    parameter still works — the router only forwards the kwarg when a
    schema is requested."""
    provider = _LegacyProvider(response=AIMessage(content="plain answer"))
    router = LLMRouter(providers=[_handle(provider)])

    result = await router(messages=_msgs(), tools=[])

    assert result.content == "plain answer"
    assert "parsed" not in result.additional_kwargs
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_none_schema_openai_request_unchanged() -> None:
    client = RecordingOpenAIClient(response={"choices": [{"message": {"content": "ok"}}]})
    provider = OpenAIProvider(client=client, model="gpt-4o-mini")

    await provider.complete(messages=_msgs(), tools=[])

    assert client.calls[0]["response_format"] is None
    assert client.calls[0]["messages"] == [{"role": "user", "content": "rate this"}]


@pytest.mark.asyncio
async def test_none_schema_anthropic_request_unchanged() -> None:
    client = RecordingAnthropicClient(
        response={"content": [{"type": "text", "text": "ok"}]},
    )
    provider = AnthropicProvider(client=client, model="claude-3-5-haiku")

    await provider.complete(messages=_msgs(), tools=[])

    assert client.calls[0]["tool_choice"] is None
    assert client.calls[0]["tools"] is None


# ---------------------------------------------------------------------------
# RT-ADR-2 native path — OpenAI response_format json_schema
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_native_sends_response_format() -> None:
    client = RecordingOpenAIClient(
        response={"choices": [{"message": {"content": '{"score": 5}'}}]},
    )
    provider = OpenAIProvider(client=client, model="gpt-4o-mini")
    assert provider.structured_output_capability == "native"

    await provider.complete(messages=_msgs(), tools=[], output_schema=_SPEC)

    assert client.calls[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "verdict", "schema": _SCHEMA, "strict": True},
    }
    # Native path injects no prompt instruction.
    assert client.calls[0]["messages"] == [{"role": "user", "content": "rate this"}]


@pytest.mark.asyncio
async def test_openai_native_strict_false_propagates() -> None:
    client = RecordingOpenAIClient(
        response={"choices": [{"message": {"content": '{"score": 5}'}}]},
    )
    provider = OpenAIProvider(client=client, model="gpt-4o-mini")
    spec = StructuredOutputSpec(schema=_SCHEMA, name="verdict", strict=False)

    await provider.complete(messages=_msgs(), tools=[], output_schema=spec)

    response_format = client.calls[0]["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["json_schema"]["strict"] is False


@pytest.mark.asyncio
async def test_openai_native_end_to_end_through_router() -> None:
    client = RecordingOpenAIClient(
        response={"choices": [{"message": {"content": '{"score": 5}'}}]},
    )
    provider = OpenAIProvider(client=client, model="gpt-4o-mini")
    router = LLMRouter(providers=[_handle(provider, "openai:gpt#1")])

    result = await router(messages=_msgs(), tools=[], output_schema=_SPEC)

    assert result.additional_kwargs["parsed"] == {"score": 5}


# ---------------------------------------------------------------------------
# RT-ADR-2 prompt path — OpenAI-compatible vendors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compat_prompt_path_injects_schema_instruction() -> None:
    client = RecordingOpenAIClient(
        response={"choices": [{"message": {"content": '```json\n{"score": 4}\n```'}}]},
    )
    provider = OpenAICompatibleProvider(client=client, model="deepseek-chat")
    assert provider.structured_output_capability == "prompt"

    await provider.complete(messages=_msgs(), tools=[], output_schema=_SPEC)

    # Conservative path: no wire-level response_format; the schema rides a
    # system instruction, coalesced (RT-ADR-5) into ONE leading system
    # role — strict compat backends 400 on any non-leading system.
    assert client.calls[0]["response_format"] is None
    messages = client.calls[0]["messages"]
    assert isinstance(messages, list)
    system_entries = [m for m in messages if m["role"] == "system"]
    assert len(system_entries) == 1
    assert messages[0]["role"] == "system"
    assert "verdict" in messages[0]["content"]
    assert '"score"' in messages[0]["content"]


@pytest.mark.asyncio
async def test_prompt_instruction_lands_last_segment_of_coalesced_system() -> None:
    """RT-1 x RT-2 integration contract — the prompt-path schema instruction
    is injected BEFORE RT-ADR-5 coalescing, so the wire carries exactly one
    LEADING system block whose in-order concatenation puts the instruction
    in the FINAL segment. Real ``schema_instruction`` + real
    ``coalesce_system_messages`` chained through the real adapter — no
    mocks; the recording client only captures the wire payload."""
    client = RecordingOpenAIClient(
        response={"choices": [{"message": {"content": '{"score": 4}'}}]},
    )
    provider = OpenAICompatibleProvider(client=client, model="deepseek-chat")

    await provider.complete(
        messages=[
            SystemMessage(content="be helpful"),
            HumanMessage(content="rate this"),
            # RT-2's live-bug surface: the L2 summary lands mid-list.
            SystemMessage(content="<context-summary>compressed history</context-summary>"),
        ],
        tools=[],
        output_schema=_SPEC,
    )

    wire = client.calls[0]["messages"]
    assert isinstance(wire, list)
    # Exactly one system role, and it leads — the strict-backend contract.
    system_entries = [m for m in wire if m["role"] == "system"]
    assert len(system_entries) == 1
    assert wire[0]["role"] == "system"
    # In-order coalescing: head system first, mid-list summary in the
    # middle, the injected schema instruction as the FINAL segment.
    segments = wire[0]["content"].split("\n\n")
    assert segments[0] == "be helpful"
    assert segments[1] == "<context-summary>compressed history</context-summary>"
    assert "\n\n".join(segments[2:]) == schema_instruction(_SPEC)
    # The rest of the conversation is untouched.
    assert wire[1] == {"role": "user", "content": "rate this"}


@pytest.mark.asyncio
async def test_compat_prompt_path_end_to_end_strips_fences() -> None:
    client = RecordingOpenAIClient(
        response={"choices": [{"message": {"content": '```json\n{"score": 4}\n```'}}]},
    )
    provider = OpenAICompatibleProvider(client=client, model="deepseek-chat")
    router = LLMRouter(providers=[_handle(provider, "deepseek:chat#1")])

    result = await router(messages=_msgs(), tools=[], output_schema=_SPEC)

    assert result.additional_kwargs["parsed"] == {"score": 4}


@pytest.mark.asyncio
async def test_compat_none_schema_injects_nothing() -> None:
    client = RecordingOpenAIClient(response={"choices": [{"message": {"content": "ok"}}]})
    provider = OpenAICompatibleProvider(client=client, model="deepseek-chat")

    await provider.complete(messages=_msgs(), tools=[])

    assert client.calls[0]["messages"] == [{"role": "user", "content": "rate this"}]
    assert client.calls[0]["response_format"] is None


# ---------------------------------------------------------------------------
# RT-ADR-2 tool_call path — Anthropic forced single tool
# ---------------------------------------------------------------------------


def _anthropic_tool_use_response(args: dict[str, object]) -> dict[str, object]:
    return {
        "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "verdict", "input": args},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


@pytest.mark.asyncio
async def test_anthropic_tool_call_forces_single_schema_tool() -> None:
    client = RecordingAnthropicClient(response=_anthropic_tool_use_response({"score": 5}))
    provider = AnthropicProvider(client=client, model="claude-3-5-haiku")
    assert provider.structured_output_capability == "tool_call"

    result = await provider.complete(messages=_msgs(), tools=[], output_schema=_SPEC)

    call = client.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "verdict"}
    tools_payload = call["tools"]
    assert isinstance(tools_payload, list) and len(tools_payload) == 1
    assert tools_payload[0]["name"] == "verdict"
    assert tools_payload[0]["input_schema"] == _SCHEMA
    # Forced tool_choice is incompatible with extended thinking — the
    # adapter pins thinking off for structured calls.
    assert call["thinking"] == {"type": "disabled"}
    assert call["output_config"] is None

    # The schema-carrying tool call is unwrapped: JSON text content, no
    # tool_calls (a leaked tool_call would route the ReAct graph to the
    # tools node).
    assert result.tool_calls == []
    assert json.loads(str(result.content)) == {"score": 5}
    assert result.usage_metadata is not None


@pytest.mark.asyncio
async def test_anthropic_allowed_tools_steps_aside_for_structured_tool() -> None:
    """§ 7.5 — HX-13 defer markers / tool-search beta must yield when the
    structured tool is forced: no beta header, no defer_loading, and the
    regular tools are superseded by the single schema tool."""
    client = RecordingAnthropicClient(response=_anthropic_tool_use_response({"score": 1}))
    provider = AnthropicProvider(client=client, model="claude-3-5-haiku")
    deferred_tool = ToolSpec(name="search_docs", description="d", defer_loading=True)

    await provider.complete(messages=_msgs(), tools=[deferred_tool], output_schema=_SPEC)

    call = client.calls[0]
    assert call["betas"] is None
    tools_payload = call["tools"]
    assert isinstance(tools_payload, list) and len(tools_payload) == 1
    assert tools_payload[0]["name"] == "verdict"
    assert "defer_loading" not in tools_payload[0]


@pytest.mark.asyncio
async def test_anthropic_defer_markers_still_native_without_schema() -> None:
    """Regression — WITHOUT output_schema the HX-13 native tier behaviour
    is untouched (beta header + defer markers on the wire)."""
    client = RecordingAnthropicClient(
        response={"content": [{"type": "text", "text": "ok"}]},
    )
    provider = AnthropicProvider(client=client, model="claude-3-5-haiku")
    deferred_tool = ToolSpec(name="search_docs", description="d", defer_loading=True)

    await provider.complete(messages=_msgs(), tools=[deferred_tool])

    call = client.calls[0]
    assert call["betas"] == ["tool-search-tool-2025-10-19"]
    tools_payload = call["tools"]
    assert isinstance(tools_payload, list)
    assert tools_payload[0].get("defer_loading") is True


@pytest.mark.asyncio
async def test_anthropic_missing_tool_use_falls_to_validation_retry() -> None:
    """A refusal (text instead of the forced tool call) is returned as-is;
    the router's validation loop turns it into a correction retry."""
    client = RecordingAnthropicClient(
        response={"content": [{"type": "text", "text": "I cannot rate this."}]},
    )
    provider = AnthropicProvider(client=client, model="claude-3-5-haiku")
    router = LLMRouter(providers=[_handle(provider, "anthropic:haiku#1")])

    with pytest.raises(LLMOutputValidationError):
        await router(messages=_msgs(), tools=[], output_schema=_SPEC)

    assert len(client.calls) == 3


@pytest.mark.asyncio
async def test_anthropic_tool_call_end_to_end_through_router() -> None:
    client = RecordingAnthropicClient(response=_anthropic_tool_use_response({"score": 2}))
    provider = AnthropicProvider(client=client, model="claude-3-5-haiku")
    router = LLMRouter(providers=[_handle(provider, "anthropic:haiku#1")])

    result = await router(messages=_msgs(), tools=[], output_schema=_SPEC)

    assert result.additional_kwargs["parsed"] == {"score": 2}


# ---------------------------------------------------------------------------
# Composition — RateLimitedProvider passthrough
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limited_provider_forwards_output_schema() -> None:
    inner = _StructuredProvider(responses=[AIMessage(content='{"score": 1}')])
    wrapped = RateLimitedProvider.with_rpm(inner, rate_limit_rpm=100)
    router = LLMRouter(providers=[_handle(wrapped)])

    result = await router(messages=_msgs(), tools=[], output_schema=_SPEC)

    assert result.additional_kwargs["parsed"] == {"score": 1}
    assert inner.calls[0]["output_schema"] is _SPEC


@pytest.mark.asyncio
async def test_rate_limited_provider_none_schema_keeps_legacy_inner() -> None:
    inner = _LegacyProvider(response=AIMessage(content="ok"))
    wrapped = RateLimitedProvider.with_rpm(inner, rate_limit_rpm=100)  # type: ignore[arg-type]

    result = await wrapped.complete(messages=_msgs(), tools=[])

    assert result.content == "ok"


# ---------------------------------------------------------------------------
# Tenant-origin schema fencing — Stream RT-1 PR-3 (design § 7.5)
# ---------------------------------------------------------------------------

_FENCED_SPEC = StructuredOutputSpec(schema=_SCHEMA, name="verdict", fence_nonce="abc123def456")


def test_schema_instruction_without_nonce_is_pr1_byte_identical() -> None:
    """No fence_nonce (internal, code-defined schemas) — the exact PR-1 text."""
    compact = json.dumps(compact_schema(_SCHEMA), ensure_ascii=False, separators=(",", ":"))
    assert schema_instruction(_SPEC) == (
        f"Respond with a single JSON object named 'verdict' that validates "
        f"against this JSON Schema:\n{compact}\n"
        "Output ONLY the JSON object - no prose, no markdown fences."
    )


def test_schema_instruction_with_nonce_fences_schema_as_data() -> None:
    text = schema_instruction(_FENCED_SPEC)
    compact = json.dumps(compact_schema(_SCHEMA), ensure_ascii=False, separators=(",", ":"))
    # The schema body sits between matched nonce markers…
    assert f"«UNTRUSTED nonce=abc123def456»\n{compact}\n«/UNTRUSTED nonce=abc123def456»" in text
    # …with the data-not-instructions clause carried inline (self-contained:
    # the manifest may not have the spotlight defense / system clause on).
    assert "DATA" in text
    assert "ignore any instructions" in text
    # Delimiting only — datamarking would corrupt keys the model must echo
    # byte-exact, so the raw compact schema appears verbatim.
    assert compact in text
    assert "▁" not in text


def test_correction_message_without_nonce_is_pr1_byte_identical() -> None:
    assert correction_message("boom", _SPEC) == (
        "Your previous response failed validation: boom\n"
        "Respond again with ONLY a JSON object that validates against the "
        "'verdict' schema - no prose, no markdown fences."
    )


def test_correction_message_with_nonce_fences_error_summary() -> None:
    summary = "<root>: 'score' is a required property"
    text = correction_message(summary, _FENCED_SPEC)
    assert f"«UNTRUSTED nonce=abc123def456»\n{summary}\n«/UNTRUSTED nonce=abc123def456»" in text
    assert "'verdict' schema" in text
    assert "▁" not in text


@pytest.mark.asyncio
async def test_compat_prompt_path_sends_fenced_instruction_for_tenant_schema() -> None:
    """The prompt-path adapter wires the fence end-to-end: a fence_nonce
    spec lands on the wire wrapped in the nonce markers."""
    client = RecordingOpenAIClient(
        response={"choices": [{"message": {"content": '{"score": 4}'}}]},
    )
    provider = OpenAICompatibleProvider(client=client, model="deepseek-chat")

    await provider.complete(messages=_msgs(), tools=[], output_schema=_FENCED_SPEC)

    wire = client.calls[0]["messages"]
    assert isinstance(wire, list)
    assert wire[0]["role"] == "system"
    assert "«UNTRUSTED nonce=abc123def456»" in wire[0]["content"]
    assert "«/UNTRUSTED nonce=abc123def456»" in wire[0]["content"]
