"""Unit + wiring tests for tool_call span input/output enrichment.

The trace detail panel can't show tool call args/result because the
orchestrator never sends them to Langfuse — ``_record_tool_io`` (builder.py)
fixes that by setting the ``langfuse.observation.input`` /
``langfuse.observation.output`` OTel attributes on the ``tool_call`` span,
masked (secrets stripped — the OTLP export path does NOT apply Langfuse's
PII mask, spike-confirmed) and capped (avoid huge Langfuse payloads).

Two layers:

* Direct unit tests against ``_record_tool_io`` with a minimal fake span —
  precise control over mask / cap / best-effort-on-exception behaviour.
* One wiring test through ``_dispatch_tool`` + ``InMemorySpanExporter``
  (same harness as ``test_react_graph_tracing.py``) proving the tool span
  block actually calls the helper with real args/result.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any
from uuid import uuid4

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from expert_work.common.observability import init_tracing
from orchestrator import ToolContext, ToolRegistry, ToolResult, ToolSpec
from orchestrator.graph_builder.builder import (
    _TOOL_IO_CAP,
    _dispatch_tool,
    _record_tool_io,
)

_INPUT_KEY = "langfuse.observation.input"
_OUTPUT_KEY = "langfuse.observation.output"


class _RecSpan:
    """Minimal fake span — records every ``set_attribute`` call."""

    def __init__(self) -> None:
        self.calls: dict[str, Any] = {}

    def set_attribute(self, key: str, value: Any) -> None:
        self.calls[key] = value


class _RaisingSpan:
    """Fake span whose ``set_attribute`` always raises."""

    def set_attribute(self, key: str, value: Any) -> None:
        raise RuntimeError("span backend unavailable")


# --- _record_tool_io: masking ------------------------------------------------


def test_record_tool_io_masks_secret_in_input_and_output() -> None:
    span = _RecSpan()
    secret_token = "sk-" + "a" * 25
    args = {"query": "contact me at foo@example.com", "token": secret_token}
    result = f"response with email bar@example.com and token {secret_token}"

    _record_tool_io(span, args, result)

    in_text = span.calls[_INPUT_KEY]
    out_text = span.calls[_OUTPUT_KEY]

    assert "foo@example.com" not in in_text
    assert secret_token not in in_text
    assert "bar@example.com" not in out_text
    assert secret_token not in out_text
    assert "***REDACTED***" in in_text
    assert "***REDACTED***" in out_text


# --- _record_tool_io: cap -----------------------------------------------------


def test_record_tool_io_caps_long_output() -> None:
    span = _RecSpan()
    huge_result = "x" * (_TOOL_IO_CAP * 2)

    _record_tool_io(span, {}, huge_result)

    out_text = span.calls[_OUTPUT_KEY]
    assert len(out_text) <= _TOOL_IO_CAP


def test_record_tool_io_caps_long_input() -> None:
    span = _RecSpan()
    huge_args = {"data": "y" * (_TOOL_IO_CAP * 2)}

    _record_tool_io(span, huge_args, "ok")

    in_text = span.calls[_INPUT_KEY]
    assert len(in_text) <= _TOOL_IO_CAP


# --- _record_tool_io: best-effort --------------------------------------------


def test_record_tool_io_does_not_raise_when_span_set_attribute_fails() -> None:
    span = _RaisingSpan()

    # Must not propagate — instrumentation is a side-channel and must never
    # break tool execution.
    _record_tool_io(span, {"q": "hi"}, "result")


def test_record_tool_io_logs_warning_on_failure(caplog: pytest.LogCaptureFixture) -> None:
    span = _RaisingSpan()

    with caplog.at_level("WARNING"):
        _record_tool_io(span, {"q": "hi"}, "result")

    assert "tool_span_io.record_failed" in caplog.text


# --- wiring: _dispatch_tool sets the attributes on the real span ------------


@pytest.fixture
def exporter() -> Iterator[InMemorySpanExporter]:
    exp = InMemorySpanExporter()
    init_tracing(
        service_name="test-tool-span-io",
        env="test",
        span_processor=SimpleSpanProcessor(exp),
    )
    exp.clear()
    yield exp
    exp.clear()


class _SecretEchoTool:
    def __init__(self, spec: ToolSpec) -> None:
        self.spec = spec

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        del ctx
        return ToolResult(content=f"leaked email: leak@example.com args={dict(args)}")


async def test_dispatch_tool_records_masked_io_on_tool_call_span(
    exporter: InMemorySpanExporter,
) -> None:
    tool = _SecretEchoTool(ToolSpec(name="secret_echo", description="d", is_read_only=True))
    registry = ToolRegistry()
    registry.register(tool)
    ctx = ToolContext(tenant_id=uuid4(), run_id=uuid4())

    await _dispatch_tool(
        {"name": "secret_echo", "id": "call-1", "args": {"contact": "user@example.com"}},
        registry,
        ctx,
        before_tool_dispatch_chain=None,
    )

    spans = [
        s for s in exporter.get_finished_spans() if s.name == "expert_work.orchestrator.tool_call"
    ]
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert attrs is not None

    in_text = str(attrs[_INPUT_KEY])
    out_text = str(attrs[_OUTPUT_KEY])
    assert "user@example.com" not in in_text
    assert "leak@example.com" not in out_text
    assert "***REDACTED***" in in_text
    assert "***REDACTED***" in out_text
