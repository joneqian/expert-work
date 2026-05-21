"""J.4 sub-agent eval — Stream J.13a (M0 baseline).

Drives :class:`~orchestrator.tools.subagent.SubAgentTool` against scripted
child runs to verify the J.4 capability — delegation, partial-result on
max_steps, cancellation propagation — *and* the Mini-ADR J-21 補强:
budget telemetry in :class:`ToolResult.meta` + L7 trajectory dispatch.

Per Mini-ADR J-37, J.4 metric is ``pass-rate`` (deterministic — no
LLM-judge). Threshold ≥ 0.80 (§ 18.3). The eval feeds the SubAgentTool
fake graphs + a fake :class:`TrajectoryRecorder`; production wiring is
covered by ``test_subagent.py`` integration tests.
"""

from __future__ import annotations

import sys as _sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from pathlib import Path as _Path
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import yaml
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from helix_agent.protocol import SubAgentSpec
from helix_agent.runtime.cancellation import CancellationToken, RunCancelledError
from orchestrator.agent_factory import BuiltAgent
from orchestrator.errors import MaxStepsExceededError
from orchestrator.tools.registry import ToolContext
from orchestrator.tools.subagent import SubAgentTool

_EVAL_DIR = _Path(__file__).resolve().parent
if str(_EVAL_DIR) not in _sys.path:
    _sys.path.insert(0, str(_EVAL_DIR))

from _capability import (  # type: ignore[import-not-found]  # noqa: E402
    CapabilityCaseResult,
    CapabilityReport,
    JudgeCompletionFn,
)

CAPABILITY = "J.4_sub_agent"
METRIC_TYPE = "pass-rate"
THRESHOLD = {"pass_rate": 0.80}


Scenario = Literal["success", "max_steps", "cancelled", "empty_answer"]
Role = Literal["human", "ai"]


@dataclass(frozen=True)
class ScriptedMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class SubAgentCase:
    """One sub-agent capability case.

    Drives :class:`SubAgentTool.call` against a scripted child graph that
    either returns ``messages`` + ``step_count`` (success / empty_answer)
    or raises (max_steps / cancelled). ``has_recorder`` controls whether
    the L7 dispatch path is exercised; ``expected_trajectory_dispatched``
    confirms the dispatch fired (or didn't) for the case.
    """

    case_id: str
    scenario: Scenario
    scripted_messages: tuple[ScriptedMessage, ...]
    scripted_step_count: int
    has_recorder: bool
    expected_outcome_label: Literal["success", "max_steps", "cancelled"]
    expected_content_contains: str
    expected_iteration_used: int
    expected_llm_call_count: int
    expected_meta_flags: tuple[str, ...] = ()
    expected_trajectory_dispatched: bool = False


# ---------------------------------------------------------------------------
# Internal fakes — match the shapes SubAgentTool consumes without dragging in
# real LangGraph compiled-graph machinery.
# ---------------------------------------------------------------------------


@dataclass
class _FakeRecorder:
    """Captures :class:`TrajectoryRecord` instances; the eval inspects them."""

    records: list[Any] = field(default_factory=list)

    async def record(self, record: Any) -> None:
        self.records.append(record)


@dataclass
class _ScriptedGraph:
    """Fake compiled child graph driving one of :class:`SubAgentCase`'s
    scenarios."""

    scenario: Scenario
    messages: list[BaseMessage]
    step_count: int

    async def ainvoke(self, state: Any, config: Any) -> Any:
        del state, config
        if self.scenario == "max_steps":
            raise MaxStepsExceededError(step_count=self.step_count, max_steps=self.step_count)
        if self.scenario == "cancelled":
            raise RunCancelledError("scripted cancellation")
        return {"messages": list(self.messages), "step_count": self.step_count}

    async def aget_state(self, config: Any) -> Any:
        del config

        @dataclass
        class _Snapshot:
            values: dict[str, Any]

        return _Snapshot(values={"messages": list(self.messages), "step_count": self.step_count})


@dataclass
class _ScriptedBuilder:
    built: BuiltAgent

    async def __call__(self, *, tenant_id: UUID, name: str, version: str, depth: int) -> BuiltAgent:
        del tenant_id, name, version, depth
        return self.built


def _to_langchain_messages(scripted: Sequence[ScriptedMessage]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for m in scripted:
        if m.role == "human":
            out.append(HumanMessage(content=m.content))
        else:
            out.append(AIMessage(content=m.content))
    return out


# ---------------------------------------------------------------------------
# Per-case runner
# ---------------------------------------------------------------------------


_SUB_SPEC = SubAgentSpec(
    name="eval_sub",
    agent_ref="eval-sub@1.0.0",
    description="J.13a eval — scripted sub-agent.",
)


async def _run_case(case: SubAgentCase) -> CapabilityCaseResult:
    messages = _to_langchain_messages(case.scripted_messages)
    graph = _ScriptedGraph(
        scenario=case.scenario,
        messages=messages,
        step_count=case.scripted_step_count,
    )
    built = BuiltAgent(graph=cast(Any, graph), system_prompt="x", max_steps=10)
    builder = _ScriptedBuilder(built=built)
    recorder = _FakeRecorder() if case.has_recorder else None
    tool = SubAgentTool(
        subagent=_SUB_SPEC,
        builder=cast(Any, builder),
        child_depth=1,
        trajectory_recorder=cast(Any, recorder),
    )
    ctx = ToolContext(tenant_id=uuid4(), cancellation_token=CancellationToken())

    notes: list[str] = []
    if case.scenario == "cancelled":
        try:
            await tool.call({"task": "x"}, ctx=ctx)
            notes.append("cancelled scenario did not raise RunCancelledError")
            return CapabilityCaseResult(case_id=case.case_id, passed=False, notes=tuple(notes))
        except RunCancelledError:
            await _drain_background_tasks()
            return _verify_post_call(case=case, result=None, recorder=recorder, raised_cancel=True)

    try:
        result = await tool.call({"task": "x"}, ctx=ctx)
    except Exception as exc:
        return CapabilityCaseResult(
            case_id=case.case_id,
            passed=False,
            notes=(f"unexpected exception: {type(exc).__name__}: {exc}",),
        )
    await _drain_background_tasks()
    return _verify_post_call(case=case, result=result, recorder=recorder, raised_cancel=False)


async def _drain_background_tasks() -> None:
    """Let SubAgentTool's fire-and-forget trajectory dispatch tasks run."""
    import asyncio  # local import — async helper only

    await asyncio.sleep(0)


def _verify_post_call(
    *,
    case: SubAgentCase,
    result: Any,
    recorder: _FakeRecorder | None,
    raised_cancel: bool,
) -> CapabilityCaseResult:
    notes: list[str] = []
    if raised_cancel:
        if case.expected_outcome_label != "cancelled":
            notes.append("cancellation raised but case did not expect 'cancelled'")
    else:
        if result is None:
            notes.append("SubAgentTool returned no result for non-cancelled scenario")
            return CapabilityCaseResult(case_id=case.case_id, passed=False, notes=tuple(notes))
        content = str(result.content)
        if case.expected_content_contains.lower() not in content.lower():
            notes.append(
                f"content missing keyword {case.expected_content_contains!r}; got {content!r}"
            )
        # Budget telemetry must always appear (Mini-ADR J-21).
        meta = result.meta
        for key in ("subagent", "iteration_used", "llm_call_count", "wall_clock_ms"):
            if key not in meta:
                notes.append(f"meta missing key {key!r}")
        if meta.get("iteration_used") != case.expected_iteration_used:
            notes.append(
                f"iteration_used: expected {case.expected_iteration_used} got "
                f"{meta.get('iteration_used')}"
            )
        if meta.get("llm_call_count") != case.expected_llm_call_count:
            notes.append(
                f"llm_call_count: expected {case.expected_llm_call_count} got "
                f"{meta.get('llm_call_count')}"
            )
        for flag in case.expected_meta_flags:
            if not meta.get(flag):
                notes.append(f"meta flag {flag!r} not set")

    if recorder is not None:
        if case.expected_trajectory_dispatched:
            if not recorder.records:
                notes.append("expected trajectory dispatch but recorder saw no records")
            else:
                rec = recorder.records[0]
                if rec.outcome != case.expected_outcome_label:
                    notes.append(
                        f"trajectory outcome: expected {case.expected_outcome_label!r} got "
                        f"{rec.outcome!r}"
                    )
        else:
            if recorder.records:
                notes.append("unexpected trajectory dispatch (recorder saw records)")
    elif case.expected_trajectory_dispatched:
        notes.append("expected trajectory dispatch but no recorder configured for this case")

    return CapabilityCaseResult(case_id=case.case_id, passed=not notes, notes=tuple(notes))


# ---------------------------------------------------------------------------
# Evaluator + loader
# ---------------------------------------------------------------------------


async def evaluate_set(
    cases: Sequence[SubAgentCase],
    *,
    judge: JudgeCompletionFn | None = None,
    rerun_count: int = 3,
) -> CapabilityReport:
    per_case: list[CapabilityCaseResult] = []
    for case in cases:
        per_case.append(await _run_case(case))
    sample = len(per_case)
    pass_rate = sum(1 for r in per_case if r.passed) / sample if sample else 0.0
    status = "PASS" if pass_rate >= THRESHOLD["pass_rate"] else "FAIL"
    return CapabilityReport(
        capability=CAPABILITY,
        metric_type=METRIC_TYPE,
        sample_size=sample,
        threshold=THRESHOLD,
        aggregate_score={"pass_rate": pass_rate},
        status=cast(Any, status),
        per_case=tuple(per_case),
    )


def load_cases(path: Path) -> list[SubAgentCase]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: list[SubAgentCase] = []
    for entry in raw.get("cases", []):
        out.append(_parse_case(entry))
    return out


def _parse_case(entry: dict[str, Any]) -> SubAgentCase:
    scripted = tuple(
        ScriptedMessage(role=cast(Any, m["role"]), content=str(m["content"]))
        for m in entry.get("scripted_messages", [])
    )
    return SubAgentCase(
        case_id=str(entry["id"]),
        scenario=cast(Any, entry["scenario"]),
        scripted_messages=scripted,
        scripted_step_count=int(entry.get("scripted_step_count", 0)),
        has_recorder=bool(entry.get("has_recorder", True)),
        expected_outcome_label=cast(Any, entry["expected_outcome_label"]),
        expected_content_contains=str(entry.get("expected_content_contains", "")),
        expected_iteration_used=int(entry.get("expected_iteration_used", 0)),
        expected_llm_call_count=int(entry.get("expected_llm_call_count", 0)),
        expected_meta_flags=tuple(str(f) for f in entry.get("expected_meta_flags", ())),
        expected_trajectory_dispatched=bool(entry.get("expected_trajectory_dispatched", False)),
    )


__all__ = [
    "CAPABILITY",
    "METRIC_TYPE",
    "THRESHOLD",
    "SubAgentCase",
    "evaluate_set",
    "load_cases",
]
