"""End-to-end tests for ``/v1/sessions`` CRUD + lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings
from helix_agent.persistence.audit_log import InMemoryAuditLogStore
from helix_agent.protocol import AuditQuery
from tests.auth_fixtures import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    build_test_jwt_verifier,
    make_test_jwt,
)

_DEFAULT_TENANT = DEFAULT_DEV_TENANT_ID

_AGENT_YAML = """\
apiVersion: helix.io/v1
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
