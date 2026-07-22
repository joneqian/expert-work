from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from expert_work.protocol import TriggerRecord
from orchestrator.tools.manage_task import ManageTaskTool
from orchestrator.tools.registry import ToolBlockedError, ToolContext


class _FakeStore:
    """In-memory stand-in for TriggerStore — only the methods manage_task uses."""

    def __init__(self) -> None:
        self.rows: dict[UUID, TriggerRecord] = {}

    async def create(self, record: TriggerRecord) -> TriggerRecord:
        for r in self.rows.values():
            if (r.tenant_id, r.agent_name, r.user_id, r.name) == (
                record.tenant_id,
                record.agent_name,
                record.user_id,
                record.name,
            ):
                raise ValueError("duplicate")
        self.rows[record.id] = record
        return record

    async def list_by_user(self, *, tenant_id, user_id, agent_name=None):
        return sorted(
            (
                r
                for r in self.rows.values()
                if r.tenant_id == tenant_id
                and r.user_id == user_id
                and (agent_name is None or r.agent_name == agent_name)
            ),
            key=lambda r: r.created_at,
        )

    async def get(self, *, trigger_id, tenant_id):
        r = self.rows.get(trigger_id)
        return r if r is not None and r.tenant_id == tenant_id else None

    async def update(self, record: TriggerRecord) -> bool:
        if record.id in self.rows:
            self.rows[record.id] = record
            return True
        return False

    async def delete(self, *, trigger_id, tenant_id) -> bool:
        r = self.rows.get(trigger_id)
        if r is not None and r.tenant_id == tenant_id:
            del self.rows[trigger_id]
            return True
        return False


def _tool(store: _FakeStore) -> ManageTaskTool:
    return ManageTaskTool(store=store, agent_name="news-bot", agent_version="1")  # type: ignore[arg-type]


def _ctx(
    *,
    tenant_id: UUID,
    user_id: UUID | None,
    thread_id: UUID | None = None,
    trigger_origin: bool = False,
) -> ToolContext:
    return ToolContext(
        tenant_id=tenant_id, user_id=user_id, thread_id=thread_id, trigger_origin=trigger_origin
    )


# ---- spec / contract ----


def test_spec_name_frozen() -> None:
    assert _tool(_FakeStore()).spec.name == "manage_task"


def test_spec_has_field_descriptions() -> None:
    props = _tool(_FakeStore()).spec.parameters["properties"]
    # guidance lives in the schema, not the system prompt (D-7)
    assert props["action"]["description"]
    assert props["time"]["description"]


# ---- create ----


@pytest.mark.asyncio
async def test_create_persists_reuse_thread_row() -> None:
    store = _FakeStore()
    tenant, user, thread = uuid4(), uuid4(), uuid4()
    res = await _tool(store).call(
        {
            "action": "create",
            "instruction": "summarize AI news",
            "frequency": "daily",
            "time": {"hour": 3, "minute": 0},
        },
        ctx=_ctx(tenant_id=tenant, user_id=user, thread_id=thread),
    )
    (row,) = store.rows.values()
    assert row.user_id == user
    assert row.agent_name == "news-bot" and row.agent_version == "1"
    assert row.kind == "cron" and row.source == "api"
    assert row.context_mode == "reuse_thread"
    assert row.originating_thread_id == thread
    assert row.config["rrule"] == "FREQ=DAILY;BYHOUR=3;BYMINUTE=0;BYSECOND=0"
    assert row.config["seed_input"] == "summarize AI news"
    assert row.config["timezone"] == "UTC"
    assert "daily at 03:00" in row.config["summary"]
    assert "news" in res.content.lower() or "created" in res.content.lower()


@pytest.mark.asyncio
async def test_create_missing_time_errors_for_reask() -> None:
    store = _FakeStore()
    with pytest.raises(ValueError, match="time"):
        await _tool(store).call(
            {"action": "create", "instruction": "x", "frequency": "daily"},
            ctx=_ctx(tenant_id=uuid4(), user_id=uuid4()),
        )
    assert not store.rows  # nothing written


@pytest.mark.asyncio
async def test_create_requires_user() -> None:
    with pytest.raises(ValueError, match="user"):
        await _tool(_FakeStore()).call(
            {
                "action": "create",
                "instruction": "x",
                "frequency": "daily",
                "time": {"hour": 3, "minute": 0},
            },
            ctx=_ctx(tenant_id=uuid4(), user_id=None),
        )


@pytest.mark.asyncio
async def test_create_duplicate_name_friendly_error() -> None:
    store = _FakeStore()
    tenant, user = uuid4(), uuid4()
    args = {
        "action": "create",
        "instruction": "same name job",
        "name": "digest",
        "frequency": "daily",
        "time": {"hour": 3, "minute": 0},
    }
    await _tool(store).call(args, ctx=_ctx(tenant_id=tenant, user_id=user))
    with pytest.raises(ValueError, match="already exists"):
        await _tool(store).call(args, ctx=_ctx(tenant_id=tenant, user_id=user))


@pytest.mark.asyncio
async def test_create_once_in_past_rejected() -> None:
    with pytest.raises(ValueError, match="past"):
        await _tool(_FakeStore()).call(
            {
                "action": "create",
                "instruction": "x",
                "frequency": "once",
                "time": {"hour": 3, "minute": 0},
                "start_date": "2000-01-01",
            },
            ctx=_ctx(tenant_id=uuid4(), user_id=uuid4()),
        )


# ---- list ----


@pytest.mark.asyncio
async def test_list_shows_user_tasks() -> None:
    store = _FakeStore()
    tenant, user = uuid4(), uuid4()
    await _tool(store).call(
        {
            "action": "create",
            "instruction": "job A",
            "name": "aaa",
            "frequency": "daily",
            "time": {"hour": 3, "minute": 0},
        },
        ctx=_ctx(tenant_id=tenant, user_id=user),
    )
    res = await _tool(store).call({"action": "list"}, ctx=_ctx(tenant_id=tenant, user_id=user))
    assert "aaa" in res.content


@pytest.mark.asyncio
async def test_list_empty() -> None:
    res = await _tool(_FakeStore()).call(
        {"action": "list"}, ctx=_ctx(tenant_id=uuid4(), user_id=uuid4())
    )
    assert "no scheduled tasks" in res.content.lower()


# ---- update / cancel ownership ----


@pytest.mark.asyncio
async def test_update_other_users_task_rejected() -> None:
    store = _FakeStore()
    tenant, owner, attacker = uuid4(), uuid4(), uuid4()
    await _tool(store).call(
        {
            "action": "create",
            "instruction": "owned",
            "name": "owned",
            "frequency": "daily",
            "time": {"hour": 3, "minute": 0},
        },
        ctx=_ctx(tenant_id=tenant, user_id=owner),
    )
    (tid,) = store.rows.keys()
    with pytest.raises(ValueError, match="no such task"):
        await _tool(store).call(
            {"action": "cancel", "task_id": str(tid)},
            ctx=_ctx(tenant_id=tenant, user_id=attacker),
        )
    assert store.rows  # not deleted


@pytest.mark.asyncio
async def test_update_enabled_toggle() -> None:
    store = _FakeStore()
    tenant, user = uuid4(), uuid4()
    await _tool(store).call(
        {
            "action": "create",
            "instruction": "job",
            "name": "j",
            "frequency": "daily",
            "time": {"hour": 3, "minute": 0},
        },
        ctx=_ctx(tenant_id=tenant, user_id=user),
    )
    (tid,) = store.rows.keys()
    await _tool(store).call(
        {"action": "update", "task_id": str(tid), "enabled": False},
        ctx=_ctx(tenant_id=tenant, user_id=user),
    )
    assert store.rows[tid].enabled is False


@pytest.mark.asyncio
async def test_cancel_removes() -> None:
    store = _FakeStore()
    tenant, user = uuid4(), uuid4()
    await _tool(store).call(
        {
            "action": "create",
            "instruction": "job",
            "name": "j",
            "frequency": "daily",
            "time": {"hour": 3, "minute": 0},
        },
        ctx=_ctx(tenant_id=tenant, user_id=user),
    )
    (tid,) = store.rows.keys()
    await _tool(store).call(
        {"action": "cancel", "task_id": str(tid)}, ctx=_ctx(tenant_id=tenant, user_id=user)
    )
    assert not store.rows


# ---- self-scheduling guardrail (D-13) ----


@pytest.mark.asyncio
async def test_blocked_under_trigger_origin() -> None:
    with pytest.raises(ToolBlockedError):
        await _tool(_FakeStore()).call(
            {"action": "list"},
            ctx=_ctx(tenant_id=uuid4(), user_id=uuid4(), trigger_origin=True),
        )


@pytest.mark.asyncio
async def test_create_once_same_day_past_time_rejected() -> None:
    """A one-off for today at a time already past must be rejected, not rolled a year."""
    store = _FakeStore()
    today = datetime.now(UTC).date().isoformat()
    with pytest.raises(ValueError, match="past"):
        await _tool(store).call(
            {
                "action": "create",
                "instruction": "x",
                "frequency": "once",
                "time": {"hour": 0, "minute": 0},  # 00:00 today is always in the past
                "start_date": today,
            },
            ctx=_ctx(tenant_id=uuid4(), user_id=uuid4()),
        )
    assert not store.rows


@pytest.mark.asyncio
async def test_update_to_past_once_rejected() -> None:
    """Retargeting a task to a past one-off date/time is rejected too."""
    store = _FakeStore()
    tenant, user = uuid4(), uuid4()
    await _tool(store).call(
        {
            "action": "create",
            "instruction": "j",
            "name": "j",
            "frequency": "daily",
            "time": {"hour": 3, "minute": 0},
        },
        ctx=_ctx(tenant_id=tenant, user_id=user),
    )
    (tid,) = store.rows.keys()
    today = datetime.now(UTC).date().isoformat()
    with pytest.raises(ValueError, match="past"):
        await _tool(store).call(
            {
                "action": "update",
                "task_id": str(tid),
                "frequency": "once",
                "time": {"hour": 0, "minute": 0},
                "start_date": today,
            },
            ctx=_ctx(tenant_id=tenant, user_id=user),
        )
