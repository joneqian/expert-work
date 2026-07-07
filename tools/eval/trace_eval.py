"""Trace-based eval — P1-S2.4 (11.4).

Asserts on the **connected span tree** (10.1) a run emits, not only its
terminal output. A capability can return the right final answer while
taking a wrong path — calling a tool it should not, looping the LLM past
a budget, or swallowing an error into a failed span. Terminal-output eval
is blind to all of that; trace eval reads the span tree and checks the
*call chain*.

Two layers, kept separate so the engine stays dependency-light:

- :func:`evaluate_trace` — the pure engine. Given the finished spans from
  an OTel ``InMemorySpanExporter`` and a :class:`TraceExpectation`, it
  returns a :class:`TraceCaseResult` (pass + violation list). Depends only
  on OpenTelemetry, so it unit-tests without a graph.
- :func:`capture_spans` — a context manager that arms an in-memory
  exporter (same posture as ``test_react_graph_tracing``). Callers drive a
  run inside it (scripted LLM → no model key → CI-runnable) and hand the
  finished spans to :func:`evaluate_trace`.

Span-name matching is by suffix (``*.llm_call`` / ``*.tool_call`` /
``*.run``) so the engine is agnostic to which :class:`ExpertWorkComponent`
prefix produced the span.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field

from _capability import (  # type: ignore[import-not-found]
    CapabilityCaseResult,
    CapabilityReport,
)
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from expert_work.common.observability import init_tracing

_LLM_SUFFIX = ".llm_call"
_TOOL_SUFFIX = ".tool_call"


@dataclass(frozen=True)
class TraceExpectation:
    """What a correct run's span tree must look like.

    Every field is opt-in (a permissive default) so a case only asserts
    the chain properties it cares about.
    """

    #: ``tool`` attribute values that MUST appear on some ``*.tool_call`` span.
    expected_tools: frozenset[str] = frozenset()
    #: ``tool`` values that must NOT appear (e.g. a write tool in a read-only run).
    forbidden_tools: frozenset[str] = frozenset()
    #: Ceiling on ``*.llm_call`` spans — a runaway loop trips this.
    max_llm_calls: int | None = None
    #: Span-name suffixes that must each appear at least once (e.g. ``.run``).
    require_span_suffixes: frozenset[str] = frozenset()
    #: Fail if any captured span finished with an ERROR status.
    forbid_error_spans: bool = True


@dataclass(frozen=True)
class TraceCaseResult:
    """Outcome of evaluating one run's spans against a :class:`TraceExpectation`."""

    case_id: str
    passed: bool
    violations: tuple[str, ...] = field(default_factory=tuple)


def _tool_names(spans: Sequence[ReadableSpan]) -> list[str]:
    names: list[str] = []
    for s in spans:
        if s.name.endswith(_TOOL_SUFFIX) and s.attributes is not None:
            tool = s.attributes.get("tool")
            if isinstance(tool, str):
                names.append(tool)
    return names


def evaluate_trace(
    spans: Sequence[ReadableSpan],
    expectation: TraceExpectation,
    *,
    case_id: str,
) -> TraceCaseResult:
    """Check a run's finished spans against ``expectation`` (pure)."""
    violations: list[str] = []

    tools_seen = set(_tool_names(spans))
    missing = expectation.expected_tools - tools_seen
    if missing:
        violations.append(f"expected tools not called: {sorted(missing)}")
    forbidden = expectation.forbidden_tools & tools_seen
    if forbidden:
        violations.append(f"forbidden tools called: {sorted(forbidden)}")

    if expectation.max_llm_calls is not None:
        llm_calls = sum(1 for s in spans if s.name.endswith(_LLM_SUFFIX))
        if llm_calls > expectation.max_llm_calls:
            violations.append(
                f"llm_call count {llm_calls} exceeds budget {expectation.max_llm_calls}"
            )

    for suffix in sorted(expectation.require_span_suffixes):
        if not any(s.name.endswith(suffix) for s in spans):
            violations.append(f"required span suffix not found: {suffix!r}")

    if expectation.forbid_error_spans:
        errored = [
            s.name
            for s in spans
            if s.status is not None and s.status.status_code is StatusCode.ERROR
        ]
        if errored:
            violations.append(f"error spans present: {sorted(set(errored))}")

    return TraceCaseResult(case_id=case_id, passed=not violations, violations=tuple(violations))


@contextmanager
def capture_spans(*, service_name: str = "trace-eval") -> Iterator[InMemorySpanExporter]:
    """Arm an in-memory span exporter for the body; clear it on entry/exit.

    Same wiring as ``test_react_graph_tracing`` — a ``SimpleSpanProcessor``
    (synchronous, so spans are visible immediately after the run) feeding an
    ``InMemorySpanExporter`` the caller reads via ``get_finished_spans()``.
    """
    exporter = InMemorySpanExporter()
    init_tracing(
        service_name=service_name,
        env="eval",
        span_processor=SimpleSpanProcessor(exporter),
    )
    exporter.clear()
    try:
        yield exporter
    finally:
        exporter.clear()


def report_from_cases(
    cases: Sequence[TraceCaseResult],
    *,
    capability: str = "10.1_trace_eval",
    threshold: float = 1.0,
) -> CapabilityReport:
    """Aggregate per-case trace results into a :class:`CapabilityReport`.

    Lets trace eval slot into the same protocol the baseline suite uses:
    ``pass_rate`` over the cases, gated at ``threshold`` (default: every
    case must pass — a broken call chain is a hard fail).
    """
    pass_rate = statistics.mean(1.0 if c.passed else 0.0 for c in cases) if cases else 0.0
    status = "PASS" if cases and pass_rate >= threshold else "FAIL"
    return CapabilityReport(
        capability=capability,
        metric_type="pass-rate",
        sample_size=len(cases),
        threshold={"pass_rate": threshold},
        aggregate_score={"pass_rate": pass_rate},
        status=status,
        per_case=tuple(
            CapabilityCaseResult(case_id=c.case_id, passed=c.passed, notes=c.violations)
            for c in cases
        ),
    )


__all__ = [
    "TraceCaseResult",
    "TraceExpectation",
    "capture_spans",
    "evaluate_trace",
    "report_from_cases",
]
