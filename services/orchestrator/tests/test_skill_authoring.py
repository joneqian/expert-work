"""Stream SE (SE-3b) — in-session skill authoring builtins (Layer A)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from helix_agent.persistence.skill import InMemorySkillStore
from helix_agent.protocol import SkillStatus
from orchestrator.tools.registry import ToolContext
from orchestrator.tools.skill_authoring import (
    AuthorSkillTool,
    ForkSkillTool,
    ProposeSkillToTenantTool,
    RefineSkillTool,
    build_skill_authoring_tools,
)

AGENT = "researcher"


def _ctx(tenant_id=None, user_id=None):
    return ToolContext(
        tenant_id=tenant_id or uuid4(),
        user_id=user_id or uuid4(),
        run_id=uuid4(),
    )


# ── author_skill ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_author_creates_draft_agent_private_owned() -> None:
    store = InMemorySkillStore()
    tool = AuthorSkillTool(store=store, agent_name=AGENT)
    tid, uid = uuid4(), uuid4()
    ctx = _ctx(tid, uid)
    res = await tool.call(
        {"name": "weekly-report", "description": "do reports", "prompt_fragment": "step 1..."},
        ctx=ctx,
    )
    assert res.meta["result"] == "ok"
    skill = await store.get_skill_by_name(tenant_id=tid, name="weekly-report")
    assert skill is not None
    assert skill.status == SkillStatus.DRAFT
    assert skill.visibility == "agent_private"
    assert skill.created_by_user_id == uid
    assert skill.created_by_agent_name == AGENT
    assert skill.latest_version == 1
    v = await store.get_version_by_number(skill_id=skill.id, tenant_id=tid, version=1)
    assert v is not None and v.authored_by == "agent" and v.evolution_origin == "in_session"


@pytest.mark.asyncio
async def test_author_high_risk_flag_from_tools() -> None:
    store = InMemorySkillStore()
    tool = AuthorSkillTool(store=store, agent_name=AGENT)
    res = await tool.call(
        {
            "name": "danger",
            "description": "x",
            "prompt_fragment": "run code",
            "tool_names": ["exec_python"],
        },
        ctx=_ctx(),
    )
    assert res.meta["high_risk"] is True


@pytest.mark.asyncio
async def test_author_rejects_bad_name() -> None:
    tool = AuthorSkillTool(store=InMemorySkillStore(), agent_name=AGENT)
    with pytest.raises(ValueError, match="invalid skill name"):
        await tool.call(
            {"name": "Bad Name!", "description": "x", "prompt_fragment": "y"}, ctx=_ctx()
        )


@pytest.mark.asyncio
async def test_author_requires_user_binding() -> None:
    tool = AuthorSkillTool(store=InMemorySkillStore(), agent_name=AGENT)
    ctx = ToolContext(tenant_id=uuid4(), user_id=None, run_id=uuid4())
    with pytest.raises(ValueError, match="user-bound"):
        await tool.call({"name": "x", "description": "y", "prompt_fragment": "z"}, ctx=ctx)


@pytest.mark.asyncio
async def test_author_duplicate_name_returns_error_result() -> None:
    store = InMemorySkillStore()
    tool = AuthorSkillTool(store=store, agent_name=AGENT)
    tid = uuid4()
    args = {"name": "dup", "description": "x", "prompt_fragment": "y"}
    await tool.call(args, ctx=_ctx(tid))
    res = await tool.call(args, ctx=_ctx(tid))
    assert res.meta["result"] == "duplicate" and res.meta["is_error"] is True


# ── refine_skill ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refine_own_skill_appends_version() -> None:
    store = InMemorySkillStore()
    tid, uid = uuid4(), uuid4()
    author = AuthorSkillTool(store=store, agent_name=AGENT)
    await author.call(
        {"name": "s", "description": "d", "prompt_fragment": "v1"}, ctx=_ctx(tid, uid)
    )
    refine = RefineSkillTool(store=store, agent_name=AGENT)
    res = await refine.call({"name": "s", "prompt_fragment": "v2 better"}, ctx=_ctx(tid, uid))
    assert res.meta["result"] == "ok" and res.meta["version"] == 2


@pytest.mark.asyncio
async def test_refine_not_owned_is_forbidden() -> None:
    store = InMemorySkillStore()
    tid = uuid4()
    # authored by user A
    await AuthorSkillTool(store=store, agent_name=AGENT).call(
        {"name": "s", "description": "d", "prompt_fragment": "v1"}, ctx=_ctx(tid, uuid4())
    )
    # user B (different user) tries to refine
    refine = RefineSkillTool(store=store, agent_name=AGENT)
    res = await refine.call({"name": "s", "prompt_fragment": "hack"}, ctx=_ctx(tid, uuid4()))
    assert res.meta["result"] == "forbidden" and res.meta["is_error"] is True


@pytest.mark.asyncio
async def test_refine_other_agent_is_forbidden() -> None:
    store = InMemorySkillStore()
    tid, uid = uuid4(), uuid4()
    await AuthorSkillTool(store=store, agent_name="agent-a").call(
        {"name": "s", "description": "d", "prompt_fragment": "v1"}, ctx=_ctx(tid, uid)
    )
    # same user, DIFFERENT agent
    res = await RefineSkillTool(store=store, agent_name="agent-b").call(
        {"name": "s", "prompt_fragment": "v2"}, ctx=_ctx(tid, uid)
    )
    assert res.meta["result"] == "forbidden"


@pytest.mark.asyncio
async def test_refine_unknown_skill() -> None:
    res = await RefineSkillTool(store=InMemorySkillStore(), agent_name=AGENT).call(
        {"name": "nope", "prompt_fragment": "x"}, ctx=_ctx()
    )
    assert res.meta["result"] == "not_found"


# ── fork_skill ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fork_copies_into_agent_private() -> None:
    store = InMemorySkillStore()
    tid, uid = uuid4(), uuid4()
    # a source skill authored by another agent
    await AuthorSkillTool(store=store, agent_name="other").call(
        {
            "name": "src",
            "description": "d",
            "prompt_fragment": "body",
            "tool_names": ["web_search"],
        },
        ctx=_ctx(tid, uuid4()),
    )
    fork = ForkSkillTool(store=store, agent_name=AGENT)
    res = await fork.call({"source_name": "src", "new_name": "mine"}, ctx=_ctx(tid, uid))
    assert res.meta["result"] == "ok"
    mine = await store.get_skill_by_name(tenant_id=tid, name="mine")
    assert mine is not None
    assert mine.visibility == "agent_private"
    assert mine.created_by_user_id == uid
    assert mine.created_by_agent_name == AGENT
    assert mine.forked_from is not None


@pytest.mark.asyncio
async def test_fork_unknown_source() -> None:
    res = await ForkSkillTool(store=InMemorySkillStore(), agent_name=AGENT).call(
        {"source_name": "nope", "new_name": "x"}, ctx=_ctx()
    )
    assert res.meta["result"] == "not_found"


@pytest.mark.asyncio
async def test_fork_rejects_bad_new_name() -> None:
    with pytest.raises(ValueError, match="invalid new_name"):
        await ForkSkillTool(store=InMemorySkillStore(), agent_name=AGENT).call(
            {"source_name": "src", "new_name": "Bad!"}, ctx=_ctx()
        )


# ── propose_skill_to_tenant (SE-3c) ───────────────────────────────────────


async def _author_owned(store, tid, uid, *, agent=AGENT, name="s"):
    await AuthorSkillTool(store=store, agent_name=agent).call(
        {"name": name, "description": "d", "prompt_fragment": "v1"}, ctx=_ctx(tid, uid)
    )


@pytest.mark.asyncio
async def test_propose_opens_pending_request() -> None:
    store = InMemorySkillStore()
    tid, uid = uuid4(), uuid4()
    await _author_owned(store, tid, uid)
    res = await ProposeSkillToTenantTool(store=store, agent_name=AGENT).call(
        {"name": "s", "reason": "broadly useful"}, ctx=_ctx(tid, uid)
    )
    assert res.meta["result"] == "ok"
    skill = await store.get_skill_by_name(tenant_id=tid, name="s")
    assert skill is not None
    rows, _ = await store.list_promote_requests(tenant_id=tid, status="pending")
    assert [r.skill_id for r in rows] == [skill.id]
    # NOT promoted — stays agent_private until an admin approves.
    assert skill.visibility == "agent_private"


@pytest.mark.asyncio
async def test_propose_not_owned_forbidden() -> None:
    store = InMemorySkillStore()
    tid = uuid4()
    await _author_owned(store, tid, uuid4())  # owned by user A
    res = await ProposeSkillToTenantTool(store=store, agent_name=AGENT).call(
        {"name": "s"},
        ctx=_ctx(tid, uuid4()),  # user B
    )
    assert res.meta["result"] == "forbidden" and res.meta["is_error"] is True


@pytest.mark.asyncio
async def test_propose_unknown_skill() -> None:
    res = await ProposeSkillToTenantTool(store=InMemorySkillStore(), agent_name=AGENT).call(
        {"name": "nope"}, ctx=_ctx()
    )
    assert res.meta["result"] == "not_found"


@pytest.mark.asyncio
async def test_propose_duplicate_pending() -> None:
    store = InMemorySkillStore()
    tid, uid = uuid4(), uuid4()
    await _author_owned(store, tid, uid)
    tool = ProposeSkillToTenantTool(store=store, agent_name=AGENT)
    assert (await tool.call({"name": "s"}, ctx=_ctx(tid, uid))).meta["result"] == "ok"
    res = await tool.call({"name": "s"}, ctx=_ctx(tid, uid))
    assert res.meta["result"] == "already_pending"


# ── builder ───────────────────────────────────────────────────────────────


def test_build_skill_authoring_tools_filters_declared() -> None:
    store = InMemorySkillStore()
    tools = build_skill_authoring_tools(
        declared=["author_skill", "fork_skill", "propose_skill_to_tenant"],
        store=store,
        agent_name=AGENT,
        audit_logger=None,
    )
    names = {t.spec.name for t in tools}
    assert names == {"author_skill", "fork_skill", "propose_skill_to_tenant"}
