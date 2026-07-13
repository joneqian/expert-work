"""Unit + wiring tests for tool_call span input/output enrichment.

The trace detail panel can't show tool call args/result because the
orchestrator never sends them to Langfuse — ``_record_tool_io`` (builder.py)
fixes that by setting the ``langfuse.observation.input`` /
``langfuse.observation.output`` OTel attributes on the ``tool_call`` span,
masked (secrets stripped — the OTLP export path does NOT apply Langfuse's
PII mask, spike-confirmed) and capped (avoid huge Langfuse payloads).

``_record_tool_error`` (R1 fix) closes a related gap: ``_invoke_tool``
catches tool exceptions and returns a ``ToolMessage(status="error")``
instead of re-raising, so the ``expert_work_span`` context manager's own
exception path never fires and the span status stays ``UNSET`` — Langfuse
then shows the failed call at ``level=DEFAULT``. ``_record_tool_error``
detects the error outcome inside the span block and sets the status
explicitly so Langfuse gets ``level=ERROR`` + ``status_message``.

Three layers:

* Direct unit tests against ``_record_tool_io`` with a minimal fake span —
  precise control over mask / cap / best-effort-on-exception behaviour.
* Direct unit tests against ``_record_tool_error`` with a minimal fake span
  that records ``set_status`` calls.
* One wiring test through ``_dispatch_tool`` + ``InMemorySpanExporter``
  (same harness as ``test_react_graph_tracing.py``) proving the tool span
  block actually calls the helpers with real args/result.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import ToolMessage
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from expert_work.common.observability import init_tracing
from orchestrator import ToolContext, ToolRegistry, ToolResult, ToolSpec
from orchestrator.graph_builder.builder import (
    _TOOL_IO_CAP,
    _dispatch_tool,
    _record_tool_error,
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


class _StatusRecSpan:
    """Minimal fake span — records every ``set_status`` call."""

    def __init__(self) -> None:
        self.statuses: list[Any] = []

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, status: Any) -> None:
        self.statuses.append(status)


class _RaisingStatusSpan:
    """Fake span whose ``set_status`` always raises."""

    def set_status(self, status: Any) -> None:
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


# --- _record_tool_error: error status -----------------------------------------


def test_record_tool_error_sets_status_error_with_classified_summary() -> None:
    span = _StatusRecSpan()
    classified = SimpleNamespace(summary="SandboxTimeout: 执行超过 30s")
    msg = ToolMessage(
        content="[tool error] boom", tool_call_id="c1", status="error", name="exec_python"
    )
    outcome = (msg, {}, 0, classified)

    _record_tool_error(span, outcome)

    assert len(span.statuses) == 1
    status = span.statuses[0]
    assert status.status_code.name == "ERROR"
    assert status.description == "SandboxTimeout: 执行超过 30s"


def test_record_tool_error_falls_back_to_content_when_no_classified_error() -> None:
    span = _StatusRecSpan()
    long_content = "[tool error] " + "x" * 300
    msg = ToolMessage(content=long_content, tool_call_id="c1", status="error", name="exec_python")
    outcome = (msg, {}, 0, None)

    _record_tool_error(span, outcome)

    status = span.statuses[0]
    assert status.status_code.name == "ERROR"
    assert status.description == long_content[:200]
    assert len(status.description) == 200


# --- _record_tool_error: best-effort -------------------------------------------


def test_record_tool_error_does_not_raise_when_span_set_status_fails() -> None:
    msg = ToolMessage(content="boom", tool_call_id="c1", status="error", name="exec_python")

    # Must not propagate — instrumentation is a side-channel and must never
    # break tool execution.
    _record_tool_error(_RaisingStatusSpan(), (msg, {}, 0, None))


def test_record_tool_error_logs_warning_on_failure(caplog: pytest.LogCaptureFixture) -> None:
    msg = ToolMessage(content="boom", tool_call_id="c1", status="error", name="exec_python")

    with caplog.at_level("WARNING"):
        _record_tool_error(_RaisingStatusSpan(), (msg, {}, 0, None))

    assert "tool_span_error.record_failed" in caplog.text


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


class _FailingTool:
    def __init__(self, spec: ToolSpec) -> None:
        self.spec = spec

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        del args, ctx
        raise RuntimeError("boom")


async def test_dispatch_tool_sets_error_status_on_tool_call_span_for_failed_tool(
    exporter: InMemorySpanExporter,
) -> None:
    """R1 fix: ``_invoke_tool`` catches the exception (does not re-raise), so
    ``expert_work_span``'s own exception path never fires — the ``tool_call``
    span must still end up ``StatusCode.ERROR`` via ``_record_tool_error``.
    """
    tool = _FailingTool(ToolSpec(name="failing_tool", description="d", is_read_only=True))
    registry = ToolRegistry()
    registry.register(tool)
    ctx = ToolContext(tenant_id=uuid4(), run_id=uuid4())

    await _dispatch_tool(
        {"name": "failing_tool", "id": "call-1", "args": {}},
        registry,
        ctx,
        before_tool_dispatch_chain=None,
    )

    spans = [
        s for s in exporter.get_finished_spans() if s.name == "expert_work.orchestrator.tool_call"
    ]
    assert len(spans) == 1
    status = spans[0].status
    assert status.status_code.name == "ERROR"
    assert status.description
