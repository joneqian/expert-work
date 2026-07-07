"""Endpoint tests for ``GET /v1/agents/{name}/{version}/users``.

The M2 users rollup folds ``thread_meta`` (agent filter) through
``RunStore.aggregate_by_threads`` per user, then joins display names from
``tenant_user`` and token totals from ``token_usage`` (which carries the
agent + user columns directly). These exercise the fold, the joins, the
recency ordering, and the per-tenant scope guard against in-memory stores.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import Settings
from expert_work.persistence.audit_log import InMemoryAuditLogStore
from expert_work.persistence.token_usage_store import TokenUsageRecord
from expert_work.runtime.runs import DisconnectMode, RunInfo, RunStatus
from tests.auth_fixtures import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    build_test_jwt_verifier,
    make_test_jwt,
)

_TENANT = UUID("11111111-1111-1111-1111-111111111111")
_NOW = datetime(2026, 6, 30, 12, 0, 0, tzinfo=UTC)


def _run(
    *,
    thread_id: UUID,
    user_id: UUID | None,
    status: RunStatus,
    created_at: datetime,
) -> RunInfo:
    return RunInfo(
        run_id=uuid4(),
        tenant_id=_TENANT,
        thread_id=thread_id,
        user_id=user_id,
        status=status,
        on_disconnect=DisconnectMode.CANCEL,
        is_resume=False,
        error="boom" if status is RunStatus.ERROR else None,
        created_at=created_at,
        updated_at=created_at,
        finished_at=created_at,
        trace_id=None,
    )


@pytest.fixture
async def client_and_users() -> AsyncIterator[tuple[AsyncClient, UUID, UUID]]:
    """App seeded with agent "alpha" activity for two users.

    ``alice`` (named in tenant_user) — 2 conversations, 3 runs (1 error),
    token usage. ``bob_id`` (bare UUID, no registry row) — 1 conversation,
    newest activity. Agent "beta" has one alice conversation that must
    not leak into alpha's rollup.
    """
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
        audit_logger=build_default_audit_logger(InMemoryAuditLogStore()),
        jwt_verifier=build_test_jwt_verifier(),
    )

    threads = app.state.thread_meta_repo
    runs = app.state.run_store
    tokens = app.state.token_usage_store
    users = app.state.tenant_user_repo

    alice = await users.resolve(
        tenant_id=_TENANT, subject_type="user", subject_id="alice", display_name="Alice"
    )
    bob_id = uuid4()  # active but never registered — display_name renders null

    t_a1, t_a2, t_bob, t_beta = uuid4(), uuid4(), uuid4(), uuid4()
    for tid, uid, agent in [
        (t_a1, alice.id, "alpha"),
        (t_a2, alice.id, "alpha"),
        (t_bob, bob_id, "alpha"),
        (t_beta, alice.id, "beta"),
    ]:
        await threads.create(
            thread_id=tid,
            tenant_id=_TENANT,
            created_by="seed",
            user_id=uid,
            agent_name=agent,
            agent_version="1.0.0",
        )

    await runs.create(
        _run(thread_id=t_a1, user_id=alice.id, status=RunStatus.SUCCESS, created_at=_NOW)
    )
    await runs.create(
        _run(
            thread_id=t_a1,
            user_id=alice.id,
            status=RunStatus.ERROR,
            created_at=_NOW + timedelta(minutes=1),
        )
    )
    await runs.create(
        _run(
            thread_id=t_a2,
            user_id=alice.id,
            status=RunStatus.SUCCESS,
            created_at=_NOW + timedelta(minutes=2),
        )
    )
    await runs.create(
        _run(
            thread_id=t_bob,
            user_id=bob_id,
            status=RunStatus.SUCCESS,
            created_at=_NOW + timedelta(minutes=30),
        )
    )
    await runs.create(
        _run(thread_id=t_beta, user_id=alice.id, status=RunStatus.SUCCESS, created_at=_NOW)
    )

    # Token usage carries the agent + user columns directly — no trace join.
    await tokens.insert(
        TokenUsageRecord(
            tenant_id=_TENANT,
            agent_name="alpha",
            agent_version="1.0.0",
            model="m1",
            user_id=alice.id,
            input_tokens=100,
            output_tokens=20,
        )
    )
    await tokens.insert(
        TokenUsageRecord(
            tenant_id=_TENANT,
            agent_name="beta",  # different agent — must not count for alpha
            agent_version="1.0.0",
            model="m1",
            user_id=alice.id,
            input_tokens=999,
            output_tokens=999,
        )
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        jwt = make_test_jwt(tenant_id=_TENANT, subject=str(uuid4()))
        client.headers["Authorization"] = f"Bearer {jwt}"
        yield client, alice.id, bob_id


@pytest.mark.asyncio
async def test_rollup_folds_conversations_runs_and_tokens(
    client_and_users: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, alice_id, bob_id = client_and_users
    resp = await client.get("/v1/agents/alpha/1.0.0/users")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["total"] == 2
    items = {i["user_id"]: i for i in data["items"]}

    alice = items[str(alice_id)]
    assert alice["display_name"] == "Alice"
    assert alice["conversation_count"] == 2
    assert alice["run_count"] == 3
    assert alice["error_count"] == 1
    # Only alpha's usage counts — beta's 999s stay out.
    assert alice["tokens"]["input_tokens"] == 100
    assert alice["tokens"]["total_tokens"] == 120

    bob = items[str(bob_id)]
    assert bob["display_name"] is None
    assert bob["conversation_count"] == 1
    assert bob["run_count"] == 1
    assert bob["tokens"] is None


@pytest.mark.asyncio
async def test_rollup_orders_by_recency(
    client_and_users: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, alice_id, bob_id = client_and_users
    resp = await client.get("/v1/agents/alpha/1.0.0/users")
    ordered = [i["user_id"] for i in resp.json()["data"]["items"]]
    # bob's run is the newest (+30min) — he sorts first.
    assert ordered == [str(bob_id), str(alice_id)]


@pytest.mark.asyncio
async def test_unknown_agent_returns_empty(
    client_and_users: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, _, _ = client_and_users
    resp = await client.get("/v1/agents/nope/9.9.9/users")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"items": [], "total": 0, "cross_tenant": False}


@pytest.mark.asyncio
async def test_cross_tenant_scope_is_rejected(
    client_and_users: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, _, _ = client_and_users
    resp = await client.get("/v1/agents/alpha/1.0.0/users", params={"tenant_id": "*"})
    # "*" isn't a UUID — FastAPI validation rejects it before the handler
    # (the handler's own guard covers a system_admin whose scope resolves
    # cross-tenant by other means).
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /v1/users/{user_id} — single registry row (fast-follow)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_user_returns_display_name(
    client_and_users: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, alice_id, _ = client_and_users
    resp = await client.get(f"/v1/users/{alice_id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["user_id"] == str(alice_id)
    assert data["display_name"] == "Alice"
    assert data["subject_type"] == "user"
    assert data["last_active_at"] is not None


@pytest.mark.asyncio
async def test_get_user_unknown_is_404(
    client_and_users: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, _, bob_id = client_and_users
    # bob is active on the agent but never registered in tenant_user.
    resp = await client.get(f"/v1/users/{bob_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_user_non_admin_for_someone_else_is_403(
    client_and_users: tuple[AsyncClient, UUID, UUID],
) -> None:
    client, alice_id, _ = client_and_users
    viewer_jwt = make_test_jwt(tenant_id=_TENANT, subject="viewer-1", roles=("viewer",))
    resp = await client.get(
        f"/v1/users/{alice_id}", headers={"Authorization": f"Bearer {viewer_jwt}"}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "USER_SCOPE_FORBIDDEN"
