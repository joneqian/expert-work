"""J.4 sub-agent eval — Stream J.13a (M0 baseline).

Drives :class:`~orchestrator.tools.subagent.SubAgentTool` against scripted
child runs to verify the J.4 capability — delegation, partial-result on
max_steps, cancellation propagation — *and* the Mini-ADR J-21 補强:
budget telemetry in :class:`ToolResult.meta` + L7 trajectory dispatch.

Mini-ADR J-40 (J.4-补强-2, 2026-05-21) — extends the eval with two extra
scenarios driving the M0 parallel-fan-out contract:

* ``parallel_fanout`` — N concurrent ``SubAgentTool.call`` via
  ``asyncio.gather``; each scripted child sleeps ``child_delay_s`` so
  wall-clock proves the calls overlapped (a serial implementation
  would take ``N * delay``).
* ``cycle_detection`` — drives
  :func:`orchestrator.agent_factory.detect_subagent_cycle` against a
  manifest fixture whose ``subagents`` chain references itself; verifies
  the build-time DFS raises :class:`AgentFactoryError` with the cycle
  path.

Per Mini-ADR J-37, J.4 metric is ``pass-rate`` (deterministic — no
LLM-judge). Threshold ≥ 0.80 (§ 18.3). The eval feeds the SubAgentTool
fake graphs + a fake :class:`TrajectoryRecorder`; production wiring is
covered by ``test_subagent.py`` integration tests.
"""

from __future__ import annotations

import asyncio
import sys as _sys
import time
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
from orchestrator.agent_factory import BuiltAgent, detect_subagent_cycle
from orchestrator.errors import AgentFactoryError, MaxStepsExceededError
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


Scenario = Literal[
    "success",
    "max_steps",
    "cancelled",
    "empty_answer",
    "parallel_fanout",
    "cycle_detection",
]
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
    # Mini-ADR J-40 (J.4-补强-2) — parallel_fanout fields. ``parallel_count``
    # drives N concurrent ``tool.call`` via ``asyncio.gather``; each child
    # sleeps ``parallel_child_delay_s`` before returning. The eval verifies
    # wall-clock ≤ ``parallel_max_wall_clock_s`` (well below
    # ``parallel_count * parallel_child_delay_s`` — the serial bound).
    parallel_count: int = 1
    parallel_child_delay_s: float = 0.0
    parallel_max_wall_clock_s: float = 0.0
    # Mini-ADR J-40 — cycle_detection field. ``cycle_chain`` lists the
    # node names that form the cycle, e.g. ``("alpha", "beta", "alpha")``
    # for A→B→A. The eval builds minimal ``AgentSpec`` fixtures linking
    # them and drives :func:`detect_subagent_cycle`; the case passes when
    # :class:`AgentFactoryError` raises with ``expected_content_contains``
    # in its message.
    cycle_chain: tuple[str, ...] = ()


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
    #: Mini-ADR J-40 — when > 0 the fake child sleeps before returning so
    #: ``parallel_fanout`` cases can detect concurrency from wall-clock.
    child_delay_s: float = 0.0

    async def ainvoke(self, state: Any, config: Any) -> Any:
        del state, config
        if self.scenario == "max_steps":
            raise MaxStepsExceededError(step_count=self.step_count, max_steps=self.step_count)
        if self.scenario == "cancelled":
            raise RunCancelledError("scripted cancellation")
        if self.child_delay_s > 0:
            await asyncio.sleep(self.child_delay_s)
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

    async def __call__(
        self,
        *,
        tenant_id: UUID,
        name: str,
        version: str,
        depth: int,
        oauth_user_id: str | None = None,
    ) -> BuiltAgent:
        del tenant_id, name, version, depth, oauth_user_id
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
    # Mini-ADR J-40 — non-single-delegation scenarios use their own runner.
    if case.scenario == "parallel_fanout":
        return await _run_parallel_fanout_case(case)
    if case.scenario == "cycle_detection":
        return _run_cycle_detection_case(case)
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
    await asyncio.sleep(0)


async def _run_parallel_fanout_case(case: SubAgentCase) -> CapabilityCaseResult:
    """Mini-ADR J-40 — drive ``parallel_count`` concurrent ``tool.call``.

    Each call uses its own :class:`SubAgentTool` instance with a scripted
    child whose ``ainvoke`` sleeps ``parallel_child_delay_s`` seconds
    before returning. ``asyncio.gather`` dispatches them; wall-clock
    duration proves the calls overlapped — a serial implementation would
    take ``parallel_count * parallel_child_delay_s``.

    A separate :class:`SubAgentTool` per call mirrors the production
    contract: the ReAct ``tools`` node dispatches one ``Tool`` object
    per ``tool_call`` (see ``graph_builder/builder.py``). What the eval
    verifies is that nothing inside ``SubAgentTool.call`` serialises
    against a concurrent sibling — no shared lock, no shared sandbox
    session, no contended global state.
    """
    notes: list[str] = []
    if case.parallel_count < 2:
        notes.append(
            "parallel_fanout requires parallel_count >= 2 to detect concurrency",
        )
        return CapabilityCaseResult(case_id=case.case_id, passed=False, notes=tuple(notes))
    if case.parallel_child_delay_s <= 0:
        notes.append(
            "parallel_fanout requires parallel_child_delay_s > 0 to detect concurrency",
        )
        return CapabilityCaseResult(case_id=case.case_id, passed=False, notes=tuple(notes))

    messages = _to_langchain_messages(case.scripted_messages)
    tools: list[SubAgentTool] = []
    for _ in range(case.parallel_count):
        graph = _ScriptedGraph(
            scenario="success",
            messages=list(messages),
            step_count=case.scripted_step_count,
            child_delay_s=case.parallel_child_delay_s,
        )
        built = BuiltAgent(graph=cast(Any, graph), system_prompt="x", max_steps=10)
        tools.append(
            SubAgentTool(
                subagent=_SUB_SPEC,
                builder=cast(Any, _ScriptedBuilder(built=built)),
                child_depth=1,
                trajectory_recorder=None,
            ),
        )

    ctx = ToolContext(tenant_id=uuid4(), cancellation_token=CancellationToken())
    started = time.monotonic()
    results = await asyncio.gather(
        *(t.call({"task": "x"}, ctx=ctx) for t in tools),
        return_exceptions=True,
    )
    wall_clock_s = time.monotonic() - started

    serial_lower_bound_s = case.parallel_count * case.parallel_child_delay_s
    if wall_clock_s >= serial_lower_bound_s:
        notes.append(
            f"wall_clock {wall_clock_s:.3f}s >= serial bound "
            f"{serial_lower_bound_s:.3f}s — calls did not run concurrently",
        )
    if case.parallel_max_wall_clock_s > 0 and wall_clock_s > case.parallel_max_wall_clock_s:
        notes.append(
            f"wall_clock {wall_clock_s:.3f}s > configured max "
            f"{case.parallel_max_wall_clock_s:.3f}s",
        )

    invocations: list[Any] = []
    for idx, item in enumerate(results):
        if isinstance(item, BaseException):
            notes.append(f"call #{idx} raised {type(item).__name__}: {item}")
            continue
        if case.expected_content_contains and (
            case.expected_content_contains.lower() not in str(item.content).lower()
        ):
            notes.append(
                f"call #{idx} content missing keyword "
                f"{case.expected_content_contains!r}; got {item.content!r}",
            )
        invocation_list = item.state_updates.get("subagent_invocations") if item else None
        if isinstance(invocation_list, list):
            invocations.extend(invocation_list)
        else:
            notes.append(f"call #{idx} state_updates missing subagent_invocations list")

    if len(invocations) != case.parallel_count:
        notes.append(
            f"expected {case.parallel_count} invocations across calls; got {len(invocations)}",
        )

    return CapabilityCaseResult(case_id=case.case_id, passed=not notes, notes=tuple(notes))


def _run_cycle_detection_case(case: SubAgentCase) -> CapabilityCaseResult:
    """Mini-ADR J-40 — drive build-time cycle detection on a manifest fixture.

    ``cycle_chain`` lists the node names in cycle order; the runner
    builds minimal :class:`AgentSpec` fixtures where node ``i`` declares
    node ``i+1`` (or the first node, wrapping at the end) as its
    sub-agent, then walks them via :func:`detect_subagent_cycle`. The
    case passes when :class:`AgentFactoryError` raises with
    ``expected_content_contains`` in its message; if the chain is
    deliberately acyclic the case can assert no raise by leaving
    ``expected_content_contains`` empty.
    """
    notes: list[str] = []
    chain = case.cycle_chain
    if len(chain) < 2:
        notes.append("cycle_detection requires cycle_chain with >= 2 nodes")
        return CapabilityCaseResult(case_id=case.case_id, passed=False, notes=tuple(notes))

    # Build one AgentSpec per unique node, wiring each to the next node
    # in the chain (the final node closes back to the first, producing
    # the cycle).
    unique_nodes = list(dict.fromkeys(chain))
    specs: dict[str, Any] = {}
    for idx, node_name in enumerate(unique_nodes):
        next_node = chain[(chain.index(node_name) + 1) % len(chain)]
        # The last unique node in the chain points back to chain[0].
        if idx == len(unique_nodes) - 1:
            next_node = chain[0]
        specs[node_name] = _make_eval_spec_with_subagents(node_name, next_node)

    try:
        detect_subagent_cycle(specs[chain[0]], resolve=lambda n, _v: specs.get(n))
    except AgentFactoryError as exc:
        if case.expected_content_contains and (case.expected_content_contains not in str(exc)):
            notes.append(
                f"AgentFactoryError raised but message missing "
                f"{case.expected_content_contains!r}; got {exc!s}",
            )
        return CapabilityCaseResult(case_id=case.case_id, passed=not notes, notes=tuple(notes))
    notes.append("expected AgentFactoryError but detect_subagent_cycle returned cleanly")
    return CapabilityCaseResult(case_id=case.case_id, passed=False, notes=tuple(notes))


def _make_eval_spec_with_subagents(name: str, *children: str) -> Any:
    """Build a minimal ``AgentSpec`` referencing the named child sub-agents.

    Mirrors :func:`_make_spec_with_subagents` in ``test_subagent.py``
    — kept local to the eval module so eval cases don't depend on
    orchestrator test fixtures.
    """
    from helix_agent.protocol import AgentSpec

    return AgentSpec.model_validate(
        {
            "apiVersion": "helix.io/v1",
            "kind": "Agent",
            "metadata": {"name": name, "version": "1.0.0", "tenant": "test-tenant"},
            "spec": {
                "tenant_config": {},
                "model": {
                    "provider": "anthropic",
                    "name": "claude-sonnet-4-6",
                    "api_key_ref": "secret://test",
                },
                "system_prompt": {"template": "you are an agent"},
                "sandbox": {
                    "resources": {"cpu": "1.0", "memory": "1Gi"},
                    "network": {"egress": "proxy", "allowlist": ["api.anthropic.com"]},
                    "filesystem": {"readonly_root": True, "writable": ["/workspace"]},
                },
                "subagents": [
                    {
                        "name": child,
                        "agent_ref": f"{child}@1.0.0",
                        "description": f"{child} sub-agent",
                    }
                    for child in children
                ],
            },
        }
    )


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
        parallel_count=int(entry.get("parallel_count", 1)),
        parallel_child_delay_s=float(entry.get("parallel_child_delay_s", 0.0)),
        parallel_max_wall_clock_s=float(entry.get("parallel_max_wall_clock_s", 0.0)),
        cycle_chain=tuple(str(n) for n in entry.get("cycle_chain", ())),
    )


__all__ = [
    "CAPABILITY",
    "METRIC_TYPE",
    "THRESHOLD",
    "SubAgentCase",
    "evaluate_set",
    "load_cases",
]
