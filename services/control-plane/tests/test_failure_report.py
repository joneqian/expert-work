"""Stream SE — SE-12 layered source-linked failure report (Mini-ADR SE-A20..A23).

Covers programmatic clustering, the zero-LLM rule/env path, the one-call-per-
content-cluster naming, the abstraction guard, and source-linked evidence refs.
The real ObjectStore blob persistence + governance UI are SE-12b.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from control_plane.failure_report import (
    FailureCase,
    FailureReportBuilder,
    FailureReportConfig,
)
from control_plane.skill_attribution import FailureSignal, SkillAttributor

_NOW = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)


class _FakeModel:
    """Attribution model: any non-env signal → content_error."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, *, prompt, tenant_id, model=None) -> str:
        self.calls += 1
        return "content_error"


class _FakeSummary:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def __call__(self, *, prompt, tenant_id) -> str:
        self.calls += 1
        return self.text


def _builder(summary_text="SUMMARY: wrong tool choice\nGUARD: prefer search before fetch"):
    model = _FakeModel()
    summ = _FakeSummary(summary_text)
    builder = FailureReportBuilder(
        attributor=SkillAttributor(model=model),
        summarizer=summ,
        config=FailureReportConfig(exemplars_per_cluster=2),
    )
    return builder, model, summ


def _case(task_id, *, error="", phase=None, tool_errors=(), timed_out=False, idx=0, snippet=""):
    return FailureCase(
        task_id=task_id,
        trajectory_key=f"traj/{task_id}",
        signal=FailureSignal(
            error_text=error, exit_phase=phase, tool_errors=tuple(tool_errors), timed_out=timed_out
        ),
        message_index=idx,
        snippet=snippet,
    )


async def test_env_cluster_skips_llm() -> None:
    builder, model, summ = _builder()
    cases = [_case("t1", timed_out=True), _case("t2", timed_out=True)]
    report = await builder.build(
        tenant_id=uuid4(), scope="candidate", scope_ref="c1", cases=cases, now=_NOW
    )
    assert model.calls == 0  # rule-attributed → no attribution LLM
    assert summ.calls == 0  # env cluster → no naming LLM (SE-A21)
    assert len(report.clusters) == 1
    assert report.clusters[0].kind == "execution_error"
    assert "not a skill-content fix" in report.clusters[0].suggested_guard


async def test_content_cluster_named_once() -> None:
    builder, model, summ = _builder()
    cases = [
        _case("t1", error="bad answer", phase="solve"),
        _case("t2", error="bad answer", phase="solve"),
    ]
    report = await builder.build(
        tenant_id=uuid4(), scope="candidate", scope_ref="c1", cases=cases, now=_NOW
    )
    assert model.calls == 2  # two content cases attributed via LLM
    assert summ.calls == 1  # single cluster → one naming call
    cl = report.clusters[0]
    assert cl.kind == "content_error"
    assert cl.summary == "wrong tool choice"
    assert cl.suggested_guard == "prefer search before fetch"
    assert cl.n_tasks == 2


async def test_distinct_buckets_cluster_separately() -> None:
    builder, _model, summ = _builder()
    cases = [
        _case("t1", error="x", phase="plan"),
        _case("t2", error="y", phase="solve"),  # different phase → different bucket
    ]
    report = await builder.build(
        tenant_id=uuid4(), scope="candidate", scope_ref="c1", cases=cases, now=_NOW
    )
    assert len(report.clusters) == 2
    assert summ.calls == 2


async def test_abstraction_guard_drops_overspecific_summary() -> None:
    builder, _model, _summ = _builder(summary_text="SUMMARY: run 123456789012345 failed\nGUARD: x")
    report = await builder.build(
        tenant_id=uuid4(),
        scope="candidate",
        scope_ref="c1",
        cases=[_case("t1", error="e", phase="solve")],
        now=_NOW,
    )
    cl = report.clusters[0]
    assert "123456789012345" not in cl.summary  # leaked id rejected → template
    assert cl.suggested_guard == ""


async def test_evidence_ref_links_to_trajectory() -> None:
    builder, _model, _summ = _builder()
    report = await builder.build(
        tenant_id=uuid4(),
        scope="candidate",
        scope_ref="c1",
        cases=[_case("t1", error="e", phase="solve", idx=7, snippet="boom" * 200)],
        now=_NOW,
    )
    detail = report.details[0]
    assert detail.task_id == "t1"
    ref = detail.evidence_refs[0]
    assert ref.trajectory_key == "traj/t1"
    assert ref.message_index == 7
    assert len(ref.snippet) <= 240  # capped


async def test_report_round_trips_as_json() -> None:
    builder, _model, _summ = _builder()
    report = await builder.build(
        tenant_id=None,
        scope="skill_version",
        scope_ref="sk@3",
        cases=[_case("t1", timed_out=True)],
        now=_NOW,
    )
    blob = report.model_dump_json()
    from control_plane.failure_report import FailureReport

    restored = FailureReport.model_validate_json(blob)
    assert restored.scope == "skill_version"
    assert restored.n_tasks_total == 1
