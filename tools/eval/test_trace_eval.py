"""Tests for trace-based eval — P1-S2.4 (11.4).

Two halves:

- Integration: drive a scripted react graph (no model key — deterministic
  LLM) under ``capture_spans`` and assert ``evaluate_trace`` reads the real
  span tree. This is the trace-eval capability running end to end in CI.
- Pure: feed hand-built spans to ``evaluate_trace`` to cover the error-span
  and require-suffix branches the scripted graph doesn't naturally emit.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import StatusCode

from expert_work.runtime.checkpointer import make_checkpointer
from orchestrator import (
    GraphRunner,
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    build_react_graph,
)

_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from trace_eval import (  # noqa: E402
    TraceExpectation,
    capture_spans,
    evaluate_trace,
    report_from_cases,
)


@dataclass
class _EchoTool:
    name: str = "echo"
    is_read_only: bool = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description="echoes", is_read_only=self.is_read_only)

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        del ctx
        return ToolResult(content=f"{self.name}:{args.get('q', '')}")


@dataclass
class _ScriptedLLM:
    responses: list[AIMessage]
    calls: int = 0

    async def __call__(
        self, *, messages: Sequence[BaseMessage], tools: Sequence[ToolSpec]
    ) -> AIMessage:
        del messages, tools
        idx = self.calls
        self.calls += 1
        return self.responses[idx]


def _tc(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


async def _run_echo_scenario() -> list[ReadableSpan]:
    """One tool turn then a final answer; return the captured spans."""
    llm = _ScriptedLLM(
        responses=[
            AIMessage(content="", tool_calls=[_tc("echo", {"q": "hi"}, "tc-1")]),
            AIMessage(content="done"),
        ]
    )
    registry = ToolRegistry()
    registry.register(_EchoTool())
    with capture_spans() as exporter:
        async with make_checkpointer("memory") as cp:
            runner = GraphRunner(checkpointer=cp)
            compiled = runner.compile(build_react_graph(llm_caller=llm, tool_registry=registry))
            cfg: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
            await compiled.ainvoke(
                {"messages": [HumanMessage(content="start")], "step_count": 0, "max_steps": 5},
                config=cfg,
            )
        return list(exporter.get_finished_spans())


@pytest.mark.asyncio
async def test_happy_chain_passes() -> None:
    spans = await _run_echo_scenario()
    result = evaluate_trace(
        spans,
        TraceExpectation(
            expected_tools=frozenset({"echo"}),
            max_llm_calls=2,
            require_span_suffixes=frozenset({".tool_call"}),
        ),
        case_id="happy",
    )
    assert result.passed, result.violations


@pytest.mark.asyncio
async def test_missing_expected_tool_fails() -> None:
    spans = await _run_echo_scenario()
    result = evaluate_trace(
        spans,
        TraceExpectation(expected_tools=frozenset({"search"})),
        case_id="missing",
    )
    assert not result.passed
    assert any("expected tools not called" in v for v in result.violations)


@pytest.mark.asyncio
async def test_llm_budget_exceeded_fails() -> None:
    spans = await _run_echo_scenario()  # 2 llm calls
    result = evaluate_trace(spans, TraceExpectation(max_llm_calls=1), case_id="budget")
    assert not result.passed
    assert any("exceeds budget" in v for v in result.violations)


@pytest.mark.asyncio
async def test_forbidden_tool_fails() -> None:
    spans = await _run_echo_scenario()
    result = evaluate_trace(
        spans,
        TraceExpectation(forbidden_tools=frozenset({"echo"})),
        case_id="forbidden",
    )
    assert not result.passed
    assert any("forbidden tools called" in v for v in result.violations)


# --- pure-engine coverage with hand-built spans -----------------------------


@dataclass(frozen=True)
class _Status:
    status_code: StatusCode


@dataclass(frozen=True)
class _FakeSpan:
    name: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    status: _Status = field(default_factory=lambda: _Status(StatusCode.UNSET))


def _spans(*spans: _FakeSpan) -> Sequence[ReadableSpan]:
    return cast(Sequence[ReadableSpan], list(spans))


def test_error_span_fails() -> None:
    spans = _spans(
        _FakeSpan("expert_work.orchestrator.tool_call", {"tool": "echo"}),
        _FakeSpan("expert_work.orchestrator.llm_call", status=_Status(StatusCode.ERROR)),
    )
    result = evaluate_trace(spans, TraceExpectation(), case_id="err")
    assert not result.passed
    assert any("error spans present" in v for v in result.violations)


def test_require_suffix_missing_fails() -> None:
    spans = _spans(_FakeSpan("expert_work.orchestrator.llm_call"))
    result = evaluate_trace(
        spans,
        TraceExpectation(require_span_suffixes=frozenset({".run"})),
        case_id="root",
    )
    assert not result.passed
    assert any("required span suffix not found" in v for v in result.violations)


def test_report_from_cases_fails_when_any_case_fails() -> None:
    cases = [
        evaluate_trace(_spans(_FakeSpan("x.llm_call")), TraceExpectation(), case_id="ok"),
        evaluate_trace(
            _spans(_FakeSpan("x.llm_call")),
            TraceExpectation(expected_tools=frozenset({"echo"})),
            case_id="bad",
        ),
    ]
    report = report_from_cases(cases)
    assert report.status == "FAIL"
    assert report.aggregate_score["pass_rate"] == 0.5
    assert report.sample_size == 2
    # Per-case violations ride along as notes for the baseline diff.
    bad = next(c for c in report.per_case if c.case_id == "bad")
    assert bad.notes
