"""End-to-end tests for ``/v1/sessions`` CRUD + lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, TypedDict

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings
from expert_work.persistence.audit_log import InMemoryAuditLogStore
from expert_work.protocol import AuditQuery
from expert_work.runtime.runs import InMemoryRunStore
from tests.agent_fixtures import stub_agent_runtime
from tests.auth_fixtures import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    build_test_jwt_verifier,
    make_test_jwt,
)


class _SeedState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


async def _seed_thread_messages(
    checkpointer: InMemorySaver, thread_id: str, messages: list[BaseMessage]
) -> None:
    """Write one checkpoint holding ``messages`` for ``thread_id`` (mirrors a
    real run leaving a durable checkpoint the backfill can read)."""
    graph = StateGraph(_SeedState)
    graph.add_node("n", lambda _state: {"messages": []})
    graph.add_edge(START, "n")
    seeded = graph.compile(checkpointer=checkpointer)
    await seeded.ainvoke(
        {"messages": messages},
        config={"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
    )


_DEFAULT_TENANT = DEFAULT_DEV_TENANT_ID

_AGENT_YAML = """\
apiVersion: expert_work.io/v1
kind: Agent
metadata:
  name: code-reviewer
  version: "1.0.0"
  tenant: platform-eng
spec:
  tenant_config: {}
  model:
    provider: anthropic
    name: claude-sonnet-4-5
  system_prompt:
    template: "you are a reviewer"
  sandbox:
    resources: { cpu: "1.0", memory: "1Gi" }
    network:
      egress: proxy
      allowlist: ["api.anthropic.com"]
    filesystem:
      readonly_root: true
      writable: ["/workspace"]
"""


@pytest.fixture
def audit_store() -> InMemoryAuditLogStore:
    return InMemoryAuditLogStore()


@pytest.fixture
async def session_client(audit_store: InMemoryAuditLogStore) -> AsyncIterator[AsyncClient]:
    settings = Settings(
        env="dev",
        auth_mode="dev",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
    )
    app = create_app(
        settings=settings,
        audit_logger=build_default_audit_logger(audit_store),
        jwt_verifier=build_test_jwt_verifier(),
    )
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {make_test_jwt(tenant_id=_DEFAULT_TENANT)}"}
    async with AsyncClient(
        transport=transport,
        base_url="http://control-plane.test",
        headers=headers,
    ) as client:
        # Seed the agent so /v1/sessions can find it.
        await client.post("/v1/agents", json={"manifest_yaml": _AGENT_YAML})
        yield client


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_creates_session_for_known_agent(
    session_client: AsyncClient, audit_store: InMemoryAuditLogStore
) -> None:
    response = await session_client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
    )
    assert response.status_code == 201
    meta = response.json()["data"]
    assert meta["agent_name"] == "code-reviewer"
    assert meta["status"] == "active"
    assert meta["thread_id"]
    page = await audit_store.query(AuditQuery(tenant_id=_DEFAULT_TENANT))
    assert any(r.action.value == "session:write" for r in page.entries)


_TARGET_USER = "00000000-0000-0000-0000-0000000000aa"


@pytest.mark.asyncio
async def test_admin_can_run_session_as_another_user(
    session_client: AsyncClient, audit_store: InMemoryAuditLogStore
) -> None:
    # Playground impersonation — an admin binds the thread to a target user_id
    # so the run uses that user's workspace / memory / episodic.
    response = await session_client.post(
        "/v1/sessions",
        json={
            "agent_name": "code-reviewer",
            "agent_version": "1.0.0",
            "run_as_user_id": _TARGET_USER,
        },
    )
    assert response.status_code == 201
    assert response.json()["data"]["user_id"] == _TARGET_USER
    page = await audit_store.query(AuditQuery(tenant_id=_DEFAULT_TENANT))
    assert any(
        r.action.value == "session:write" and r.details.get("impersonated") is True
        for r in page.entries
    )


@pytest.mark.asyncio
async def test_non_admin_cannot_run_session_as_another_user(
    session_client: AsyncClient,
) -> None:
    member_token = make_test_jwt(tenant_id=_DEFAULT_TENANT, subject="plain-user", roles=("member",))
    response = await session_client.post(
        "/v1/sessions",
        json={
            "agent_name": "code-reviewer",
            "agent_version": "1.0.0",
            "run_as_user_id": _TARGET_USER,
        },
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_post_rejects_unknown_agent(session_client: AsyncClient) -> None:
    response = await session_client.post(
        "/v1/sessions",
        json={"agent_name": "no-such", "agent_version": "9.9.9"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "AGENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_post_rejects_deleted_agent(session_client: AsyncClient) -> None:
    # Soft-delete the seeded agent first.
    await session_client.delete("/v1/agents/code-reviewer/1.0.0")
    response = await session_client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "AGENT_NOT_FOUND"


# ---------------------------------------------------------------------------
# Stream R (R-9) — tenant default agent + latest-version resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_resolves_latest_version_when_omitted(session_client: AsyncClient) -> None:
    # Only name given → latest ACTIVE version of that agent is used.
    response = await session_client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer"},
    )
    assert response.status_code == 201, response.text
    meta = response.json()["data"]
    assert meta["agent_name"] == "code-reviewer"
    assert meta["agent_version"] == "1.0.0"


@pytest.mark.asyncio
async def test_post_uses_tenant_default_when_name_omitted(session_client: AsyncClient) -> None:
    # Point the tenant default at the seeded agent, then create with no agent.
    await session_client.put(
        f"/v1/tenants/{_DEFAULT_TENANT}/config",
        json={"display_name": "Dev Tenant", "default_agent_name": "code-reviewer"},
    )
    response = await session_client.post("/v1/sessions", json={})
    assert response.status_code == 201, response.text
    assert response.json()["data"]["agent_name"] == "code-reviewer"


@pytest.mark.asyncio
async def test_post_no_agent_no_default_falls_back_to_canonical(
    session_client: AsyncClient,
) -> None:
    # No name, no tenant default → platform fallback ``canonical-agent``,
    # which isn't registered here → AGENT_NOT_FOUND (not a 500).
    response = await session_client.post("/v1/sessions", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "AGENT_NOT_FOUND"


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_single_session(session_client: AsyncClient) -> None:
    create = await session_client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
    )
    thread_id = create.json()["data"]["thread_id"]
    response = await session_client.get(f"/v1/sessions/{thread_id}")
    assert response.status_code == 200
    assert response.json()["data"]["thread_id"] == thread_id


@pytest.mark.asyncio
async def test_get_session_workspace_read_only_null(session_client: AsyncClient) -> None:
    # Playground-Uplift D4 — read-only inspector. A fresh thread has no
    # provisioned workspace, so it truthfully reports null (no VM started yet).
    create = await session_client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
    )
    thread_id = create.json()["data"]["thread_id"]
    response = await session_client.get(f"/v1/sessions/{thread_id}/workspace")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["workspace"] is None
    assert data["artifacts"] == []


@pytest.mark.asyncio
async def test_get_session_workspace_404_for_unknown(session_client: AsyncClient) -> None:
    response = await session_client.get(
        "/v1/sessions/00000000-0000-0000-0000-000000000099/workspace"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_workspace_files_empty_without_supervisor(session_client: AsyncClient) -> None:
    # No supervisor wired in the test app → the browse endpoint degrades to an
    # empty list rather than erroring the inspector.
    create = await session_client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
    )
    thread_id = create.json()["data"]["thread_id"]
    response = await session_client.get(f"/v1/sessions/{thread_id}/workspace/files")
    assert response.status_code == 200
    assert response.json()["data"]["files"] == []


@pytest.mark.asyncio
async def test_workspace_file_download_rejects_path_traversal(session_client: AsyncClient) -> None:
    create = await session_client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
    )
    thread_id = create.json()["data"]["thread_id"]
    response = await session_client.get(
        f"/v1/sessions/{thread_id}/workspace/file",
        params={"path": "../../etc/passwd"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_workspace_file_download_404_without_supervisor(session_client: AsyncClient) -> None:
    create = await session_client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
    )
    thread_id = create.json()["data"]["thread_id"]
    response = await session_client.get(
        f"/v1/sessions/{thread_id}/workspace/file", params={"path": "report.pdf"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_workspace_files_and_download_with_supervisor(
    audit_store: InMemoryAuditLogStore,
) -> None:
    # Inject a recording supervisor so the browse + download paths run end to end.
    from orchestrator.tools import RecordingSupervisorClient, WorkspaceFileEntry

    settings = Settings(
        env="dev",
        auth_mode="dev",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
    )
    app = create_app(
        settings=settings,
        audit_logger=build_default_audit_logger(audit_store),
        jwt_verifier=build_test_jwt_verifier(),
    )
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {make_test_jwt(tenant_id=_DEFAULT_TENANT)}"}
    async with AsyncClient(
        transport=transport, base_url="http://control-plane.test", headers=headers
    ) as client:
        await client.post("/v1/agents", json={"manifest_yaml": _AGENT_YAML})
        # Override the lifespan-set (None) client with a recording one.
        app.state.supervisor_client = RecordingSupervisorClient(
            workspace_files=[WorkspaceFileEntry(path="report.pdf", size=2048)],
            workspace_file=b"%PDF-1.4 hello",
        )
        create = await client.post(
            "/v1/sessions",
            json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
        )
        thread_id = create.json()["data"]["thread_id"]

        listing = await client.get(f"/v1/sessions/{thread_id}/workspace/files")
        assert listing.status_code == 200
        files = listing.json()["data"]["files"]
        assert files == [{"path": "report.pdf", "size": 2048}]

        download = await client.get(
            f"/v1/sessions/{thread_id}/workspace/file", params={"path": "report.pdf"}
        )
        assert download.status_code == 200
        assert download.content == b"%PDF-1.4 hello"
        assert "attachment" in download.headers["content-disposition"]
        assert "report.pdf" in download.headers["content-disposition"]

        # Delete proxies the path through to the supervisor (thread-scoped).
        deleted = await client.request(
            "DELETE", f"/v1/sessions/{thread_id}/workspace/file", params={"path": "report.pdf"}
        )
        assert deleted.status_code == 200
        assert deleted.json()["data"]["deleted"] == "report.pdf"
        sup = app.state.supervisor_client
        assert [d[2] for d in sup.workspace_deletes] == ["report.pdf"]

        # A traversal path is rejected before reaching the supervisor.
        bad = await client.request(
            "DELETE", f"/v1/sessions/{thread_id}/workspace/file", params={"path": "../etc/passwd"}
        )
        assert bad.status_code == 400
        assert [d[2] for d in sup.workspace_deletes] == ["report.pdf"]


@pytest.mark.asyncio
async def test_get_returns_404_for_unknown(session_client: AsyncClient) -> None:
    response = await session_client.get("/v1/sessions/00000000-0000-0000-0000-000000000099")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_filters_by_status(session_client: AsyncClient) -> None:
    for _ in range(3):
        await session_client.post(
            "/v1/sessions",
            json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
        )
    response = await session_client.get("/v1/sessions?status=active")
    assert response.status_code == 200
    assert response.json()["data"]["total"] == 3


# ---------------------------------------------------------------------------
# pause / resume / cancel state machine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_then_resume(session_client: AsyncClient) -> None:
    create = await session_client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
    )
    tid = create.json()["data"]["thread_id"]
    pause = await session_client.post(f"/v1/sessions/{tid}:pause", json={})
    assert pause.status_code == 200
    assert pause.json()["data"]["status"] == "paused"

    resume = await session_client.post(f"/v1/sessions/{tid}:resume", json={"reason": "ack"})
    assert resume.status_code == 200
    assert resume.json()["data"]["status"] == "active"


@pytest.mark.asyncio
async def test_resume_from_active_returns_409(session_client: AsyncClient) -> None:
    create = await session_client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
    )
    tid = create.json()["data"]["thread_id"]
    response = await session_client.post(f"/v1/sessions/{tid}:resume", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SESSION_STATE_CONFLICT"


@pytest.mark.asyncio
async def test_pause_terminal_session_returns_409(session_client: AsyncClient) -> None:
    create = await session_client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
    )
    tid = create.json()["data"]["thread_id"]
    await session_client.post(f"/v1/sessions/{tid}:cancel", json={})
    response = await session_client.post(f"/v1/sessions/{tid}:pause", json={})
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_cancel_emits_session_cancel_audit(
    session_client: AsyncClient, audit_store: InMemoryAuditLogStore
) -> None:
    create = await session_client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
    )
    tid = create.json()["data"]["thread_id"]
    response = await session_client.post(
        f"/v1/sessions/{tid}:cancel", json={"reason": "user_abort"}
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "cancelled"

    page = await audit_store.query(AuditQuery(tenant_id=_DEFAULT_TENANT))
    assert any(r.action.value == "session:cancel" and r.resource_id == tid for r in page.entries)


@pytest.mark.asyncio
async def test_cancel_paused_session_succeeds(session_client: AsyncClient) -> None:
    create = await session_client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
    )
    tid = create.json()["data"]["thread_id"]
    await session_client.post(f"/v1/sessions/{tid}:pause", json={})
    response = await session_client.post(f"/v1/sessions/{tid}:cancel", json={})
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_transition_404_for_unknown_thread(session_client: AsyncClient) -> None:
    response = await session_client.post(
        "/v1/sessions/00000000-0000-0000-0000-000000000099:pause",
        json={},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_other_tenant_cannot_see_session(session_client: AsyncClient) -> None:
    from uuid import UUID

    create = await session_client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
    )
    tid = create.json()["data"]["thread_id"]
    other_tenant = UUID("11111111-1111-1111-1111-111111111111")
    other_jwt = make_test_jwt(tenant_id=other_tenant)
    response = await session_client.get(
        f"/v1/sessions/{tid}",
        headers={"Authorization": f"Bearer {other_jwt}"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# per-user isolation — Stream J.14
# ---------------------------------------------------------------------------


def _user_headers(subject: str) -> dict[str, str]:
    """Bearer headers for a non-admin user in the default tenant."""
    jwt = make_test_jwt(tenant_id=_DEFAULT_TENANT, subject=subject, roles=("viewer",))
    return {"Authorization": f"Bearer {jwt}"}


async def _create_as(client: AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
        headers=headers,
    )
    assert response.status_code == 201
    return str(response.json()["data"]["thread_id"])


@pytest.mark.asyncio
async def test_create_stamps_owning_user(session_client: AsyncClient) -> None:
    response = await session_client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
        headers=_user_headers("user-a"),
    )
    assert response.status_code == 201
    assert response.json()["data"]["user_id"] is not None


@pytest.mark.asyncio
async def test_user_cannot_see_another_users_session(session_client: AsyncClient) -> None:
    tid = await _create_as(session_client, _user_headers("user-a"))
    # The owner sees it.
    own = await session_client.get(f"/v1/sessions/{tid}", headers=_user_headers("user-a"))
    assert own.status_code == 200
    # A different user in the same tenant gets 404 — existence stays hidden.
    other = await session_client.get(f"/v1/sessions/{tid}", headers=_user_headers("user-b"))
    assert other.status_code == 404


@pytest.mark.asyncio
async def test_admin_can_see_any_users_session(session_client: AsyncClient) -> None:
    tid = await _create_as(session_client, _user_headers("user-a"))
    # The default fixture headers are an admin JWT — tenant-wide access.
    response = await session_client.get(f"/v1/sessions/{tid}")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_list_scopes_to_caller_user(session_client: AsyncClient) -> None:
    await _create_as(session_client, _user_headers("user-a"))
    await _create_as(session_client, _user_headers("user-a"))
    await _create_as(session_client, _user_headers("user-b"))

    a_list = await session_client.get("/v1/sessions", headers=_user_headers("user-a"))
    assert a_list.json()["data"]["total"] == 2
    b_list = await session_client.get("/v1/sessions", headers=_user_headers("user-b"))
    assert b_list.json()["data"]["total"] == 1
    # Admin sees every thread in the tenant.
    admin_list = await session_client.get("/v1/sessions")
    assert admin_list.json()["data"]["total"] == 3


@pytest.mark.asyncio
async def test_user_cannot_transition_another_users_session(
    session_client: AsyncClient,
) -> None:
    tid = await _create_as(session_client, _user_headers("user-a"))
    intruder = await session_client.post(
        f"/v1/sessions/{tid}:pause", json={}, headers=_user_headers("user-b")
    )
    assert intruder.status_code == 404
    owner = await session_client.post(
        f"/v1/sessions/{tid}:pause", json={}, headers=_user_headers("user-a")
    )
    assert owner.status_code == 200


@pytest.mark.asyncio
async def test_machine_principal_session_is_unowned(session_client: AsyncClient) -> None:
    """A service-account caller has no per-user instance — its threads
    carry no ``user_id`` and stay tenant-scoped (legacy behaviour)."""
    sa_jwt = make_test_jwt(
        tenant_id=_DEFAULT_TENANT,
        subject="sa-1",
        sub_type="service_account",
        roles=("admin",),
    )
    sa_headers = {"Authorization": f"Bearer {sa_jwt}"}
    tid = await _create_as(session_client, sa_headers)
    create_meta = await session_client.get(f"/v1/sessions/{tid}", headers=sa_headers)
    assert create_meta.json()["data"]["user_id"] is None
    # A plain user can still read an unowned thread.
    plain = await session_client.get(f"/v1/sessions/{tid}", headers=_user_headers("user-a"))
    assert plain.status_code == 200


# ---------------------------------------------------------------------------
# Session-history uplift — rename / search / archive / purge
# ---------------------------------------------------------------------------


async def _create(session_client: AsyncClient) -> str:
    response = await session_client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
    )
    assert response.status_code == 201
    return str(response.json()["data"]["thread_id"])


@pytest.mark.asyncio
async def test_rename_sets_title(
    session_client: AsyncClient, audit_store: InMemoryAuditLogStore
) -> None:
    tid = await _create(session_client)
    resp = await session_client.patch(f"/v1/sessions/{tid}", json={"title": "  季度报告  "})
    assert resp.status_code == 200
    assert resp.json()["data"]["title"] == "季度报告"  # trimmed

    fetched = await session_client.get(f"/v1/sessions/{tid}")
    assert fetched.json()["data"]["title"] == "季度报告"

    page = await audit_store.query(AuditQuery(tenant_id=_DEFAULT_TENANT))
    assert any(r.action.value == "session:write" and r.resource_id == tid for r in page.entries)


@pytest.mark.asyncio
async def test_rename_rejects_blank_title(session_client: AsyncClient) -> None:
    tid = await _create(session_client)
    # Whitespace-only passes min_length=1 but strips to empty → 422.
    resp = await session_client.patch(f"/v1/sessions/{tid}", json={"title": "   "})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_rename_404_for_unknown(session_client: AsyncClient) -> None:
    resp = await session_client.patch(
        "/v1/sessions/00000000-0000-0000-0000-000000000099", json={"title": "x"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_backfills_title_from_checkpoint() -> None:
    # Pre-existing threads (created before auto-titling) have a NULL title; the
    # list backfills it from the checkpoint's first user message + persists.
    settings = Settings(
        env="dev",
        auth_mode="dev",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
    )
    run_store = InMemoryRunStore()
    checkpointer = InMemorySaver()
    runtime = stub_agent_runtime(run_store=run_store)
    runtime.durable_checkpointer = checkpointer
    app = create_app(
        settings=settings,
        audit_logger=build_default_audit_logger(InMemoryAuditLogStore()),
        jwt_verifier=build_test_jwt_verifier(),
        agent_runtime=runtime,
        run_repo=run_store,
    )
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {make_test_jwt(tenant_id=_DEFAULT_TENANT)}"}
    async with AsyncClient(
        transport=transport, base_url="http://control-plane.test", headers=headers
    ) as client:
        await client.post("/v1/agents", json={"manifest_yaml": _AGENT_YAML})
        tid = await _create(client)
        # Freshly created — no title.
        pre = await client.get(f"/v1/sessions/{tid}")
        assert pre.json()["data"]["title"] is None

        await _seed_thread_messages(checkpointer, tid, [HumanMessage(content="帮我写季度经营报告")])

        listed = await client.get("/v1/sessions")
        row = next(m for m in listed.json()["data"]["items"] if m["thread_id"] == tid)
        assert row["title"] == "帮我写季度经营报告"

        # Persisted — a follow-up single GET now carries the title too.
        after = await client.get(f"/v1/sessions/{tid}")
        assert after.json()["data"]["title"] == "帮我写季度经营报告"


@pytest.mark.asyncio
async def test_list_q_filters_by_title(session_client: AsyncClient) -> None:
    a = await _create(session_client)
    b = await _create(session_client)
    await session_client.patch(f"/v1/sessions/{a}", json={"title": "Quarterly Report"})
    await session_client.patch(f"/v1/sessions/{b}", json={"title": "今天天气"})

    hit = await session_client.get("/v1/sessions?q=report")
    ids = [m["thread_id"] for m in hit.json()["data"]["items"]]
    assert ids == [a]

    zh = await session_client.get("/v1/sessions?q=天气")
    assert [m["thread_id"] for m in zh.json()["data"]["items"]] == [b]

    none = await session_client.get("/v1/sessions?q=zzz")
    assert none.json()["data"]["items"] == []


@pytest.mark.asyncio
async def test_list_filters_by_agent_name(session_client: AsyncClient) -> None:
    a = await _create(session_client)
    matched = await session_client.get("/v1/sessions?agent_name=code-reviewer")
    assert a in {m["thread_id"] for m in matched.json()["data"]["items"]}

    other = await session_client.get("/v1/sessions?agent_name=nonexistent-agent")
    assert other.json()["data"]["items"] == []


@pytest.mark.asyncio
async def test_archive_hides_from_list_but_stays_reachable(
    session_client: AsyncClient, audit_store: InMemoryAuditLogStore
) -> None:
    keep = await _create(session_client)
    drop = await _create(session_client)

    resp = await session_client.delete(f"/v1/sessions/{drop}")
    assert resp.status_code == 200
    assert resp.json()["data"]["archived"] == drop

    default = await session_client.get("/v1/sessions")
    default_ids = {m["thread_id"] for m in default.json()["data"]["items"]}
    assert keep in default_ids
    assert drop not in default_ids

    with_archived = await session_client.get("/v1/sessions?include_archived=true")
    all_ids = {m["thread_id"] for m in with_archived.json()["data"]["items"]}
    assert {keep, drop} <= all_ids

    # A direct GET still resolves the archived thread (soft, reversible).
    still = await session_client.get(f"/v1/sessions/{drop}")
    assert still.status_code == 200
    assert still.json()["data"]["status"] == "archived"

    page = await audit_store.query(AuditQuery(tenant_id=_DEFAULT_TENANT))
    assert any(r.action.value == "session:write" and r.resource_id == drop for r in page.entries)


@pytest.mark.asyncio
async def test_archive_404_for_unknown(session_client: AsyncClient) -> None:
    resp = await session_client.delete("/v1/sessions/00000000-0000-0000-0000-000000000099")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_non_owner_cannot_rename_or_archive(session_client: AsyncClient) -> None:
    # Owned by user-a; user-b (a different plain user) must get 404 on both.
    tid = await _create_as(session_client, _user_headers("user-a"))
    b = _user_headers("user-b")
    rename = await session_client.patch(f"/v1/sessions/{tid}", json={"title": "x"}, headers=b)
    assert rename.status_code == 404
    archive = await session_client.delete(f"/v1/sessions/{tid}", headers=b)
    assert archive.status_code == 404
