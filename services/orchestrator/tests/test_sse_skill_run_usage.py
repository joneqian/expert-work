"""SE-7d-3b-ii — sse.py run-end ``skill_run_usage`` emission helper.

The fire-and-forget dispatch happens in a background task; here we drive the
awaitable body (``_record_skill_run_usage_safe``) directly and assert it emits
one record per bound distilled skill with the run's terminal outcome, and that
the dispatch is a no-op when nothing is wired.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from expert_work.common.skill_run_usage import BoundDistilledSkill
from expert_work.runtime.runs import RunRecord, RunStatus
from orchestrator.sse import _dispatch_skill_run_usage, _record_skill_run_usage_safe

_TENANT = UUID("77777777-7777-7777-7777-777777777777")
_THREAD = UUID("88888888-8888-8888-8888-888888888888")


class _FakeRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def record(self, **kw: object) -> None:
        self.calls.append(kw)


def _record(*skills: BoundDistilledSkill) -> RunRecord:
    return RunRecord(
        run_id=uuid4(),
        thread_id=_THREAD,
        tenant_id=_TENANT,
        status=RunStatus.RUNNING,
        bound_distilled_skills=skills,
    )


async def test_emits_one_row_per_bound_skill() -> None:
    sid_a, sid_b = uuid4(), uuid4()
    rec = _record(
        BoundDistilledSkill(skill_id=sid_a, skill_version=1, agent_name="assistant"),
        BoundDistilledSkill(skill_id=sid_b, skill_version=3, agent_name="assistant"),
    )
    recorder = _FakeRecorder()

    await _record_skill_run_usage_safe(recorder, rec, outcome="failed")

    assert len(recorder.calls) == 2
    assert {c["skill_id"] for c in recorder.calls} == {sid_a, sid_b}
    for c in recorder.calls:
        assert c["tenant_id"] == _TENANT
        assert c["thread_id"] == _THREAD
        assert c["agent_name"] == "assistant"
        assert c["outcome"] == "failed"


async def test_dispatch_noop_without_recorder() -> None:
    # No recorder wired → returns immediately, schedules nothing (no raise).
    _dispatch_skill_run_usage(
        None,
        _record(BoundDistilledSkill(skill_id=uuid4(), skill_version=1, agent_name="a")),
        outcome="success",
    )


def test_dispatch_noop_without_bound_skills() -> None:
    # Recorder wired but the run bound no distilled skills → no task scheduled.
    _dispatch_skill_run_usage(_FakeRecorder(), _record(), outcome="success")
