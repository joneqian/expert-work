"""Tests for the SE-7d-3b-ii control-plane run-usage recorder."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from control_plane.skill_run_usage_recorder import StoreSkillRunUsageRecorder
from helix_agent.common.skill_run_usage import SkillRunUsageRecorder
from helix_agent.persistence.skill.memory import InMemorySkillStore

_TENANT = UUID("66666666-6666-6666-6666-666666666666")
_NOW = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)


def test_satisfies_protocol() -> None:
    rec = StoreSkillRunUsageRecorder(store=InMemorySkillStore())
    assert isinstance(rec, SkillRunUsageRecorder)


async def test_record_writes_row() -> None:
    store = InMemorySkillStore()
    sid = uuid4()
    rec = StoreSkillRunUsageRecorder(store=store, clock=lambda: _NOW)

    await rec.record(
        skill_id=sid,
        skill_version=2,
        tenant_id=_TENANT,
        agent_name="assistant",
        thread_id=uuid4(),
        outcome="failed",
    )

    rows = await store.skill_run_usage_window(
        skill_id=sid, skill_version=2, tenant_id=_TENANT, since=_NOW - timedelta(hours=1)
    )
    assert [r.outcome for r in rows] == ["failed"]


async def test_record_swallows_store_errors() -> None:
    class BoomStore(InMemorySkillStore):
        async def record_skill_run_usage(self, *, usage):  # type: ignore[override]
            raise RuntimeError("db down")

    rec = StoreSkillRunUsageRecorder(store=BoomStore())
    # Must not raise — best-effort on the run hot path.
    await rec.record(
        skill_id=uuid4(),
        skill_version=1,
        tenant_id=_TENANT,
        agent_name="a",
        thread_id=uuid4(),
        outcome="success",
    )
