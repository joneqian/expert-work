"""Endpoint tests for ``GET /v1/conversations`` (+ ``/{thread_id}``).

The conversation view groups ``agent_run`` rows by ``thread_id`` (the
``thread_meta`` conversation) and joins ``token_usage`` by ``trace_id``.
These exercise the rollup (run/error/pending counts, token sums), the
agent / user filters, and the detail run list against in-memory stores.
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
from helix_agent.persistence.audit_log import InMemoryAuditLogStore
from helix_agent.persistence.token_usage_store import TokenUsageRecord
from helix_agent.runtime.runs import DisconnectMode, RunInfo, RunStatus
from tests.auth_fixtures import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    build_test_jwt_verifier,
    make_test_jwt,
)

_TENANT = UUID("11111111-1111-1111-1111-111111111111")
_USER_A = UUID("aaaaaaaa-0000-0000-0000-000000000001")
_USER_B = UUID("bbbbbbbb-0000-0000-0000-000000000002")
_NOW = datetime(2026, 6, 30, 12, 0, 0, tzinfo=UTC)


def _run(
    *,
    thread_id: UUID,
    user_id: UUID | None,
    status: RunStatus,
    trace_id: str | None,
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
        trace_id=trace_id,
    )


@pytest.fixture
async def client_and_threads() -> AsyncIterator[tuple[AsyncClient, dict[str, UUID]]]:
    """App seeded with 3 conversations + runs + token usage.

    ``convo`` — agent "alpha" / user A: 2 runs (1 success, 1 error), tokens.
    ``other_user`` — agent "alpha" / user B: 1 success run.
    ``other_agent`` — agent "beta" / user A: 1 success run.
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

    ids = {"convo": uuid4(), "other_user": uuid4(), "other_agent": uuid4()}
    await threads.create(
        thread_id=ids["convo"],
        tenant_id=_TENANT,
        created_by="seed",
        user_id=_USER_A,
        agent_name="alpha",
        agent_version="1.0.0",
    )
    await threads.update_title(ids["convo"], "refund question", tenant_id=_TENANT)
    await threads.create(
        thread_id=ids["other_user"],
        tenant_id=_TENANT,
        created_by="seed",
        user_id=_USER_B,
        agent_name="alpha",
        agent_version="1.0.0",
    )
    await threads.create(
        thread_id=ids["other_agent"],
        tenant_id=_TENANT,
        created_by="seed",
        user_id=_USER_A,
        agent_name="beta",
        agent_version="1.0.0",
    )

    await runs.create(
        _run(
            thread_id=ids["convo"],
            user_id=_USER_A,
            status=RunStatus.SUCCESS,
            trace_id="tr-1",
            created_at=_NOW,
        )
    )
    await runs.create(
        _run(
            thread_id=ids["convo"],
            user_id=_USER_A,
            status=RunStatus.ERROR,
            trace_id="tr-2",
            created_at=_NOW + timedelta(minutes=3),
        )
    )
    await runs.create(
        _run(
            thread_id=ids["other_user"],
            user_id=_USER_B,
            status=RunStatus.SUCCESS,
            trace_id="tr-3",
            created_at=_NOW,
        )
    )
    await runs.create(
        _run(
            thread_id=ids["other_agent"],
            user_id=_USER_A,
            status=RunStatus.SUCCESS,
            trace_id="tr-4",
            created_at=_NOW,
        )
    )

    for tid, inp, out in [("tr-1", 100, 20), ("tr-2", 50, 10)]:
        await tokens.insert(
            TokenUsageRecord(
                tenant_id=_TENANT,
                agent_name="alpha",
                agent_version="1.0.0",
                model="claude-sonnet-4-5",
                input_tokens=inp,
                output_tokens=out,
                trace_id=tid,
            )
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        jwt = make_test_jwt(tenant_id=_TENANT, subject=str(uuid4()))
        client.headers["Authorization"] = f"Bearer {jwt}"
        yield client, ids


@pytest.mark.asyncio
async def test_list_rolls_up_runs_and_tokens(
    client_and_threads: tuple[AsyncClient, dict[str, UUID]],
) -> None:
    client, ids = client_and_threads
    resp = await client.get("/v1/conversations")
    assert resp.status_code == 200
    items = {i["thread_id"]: i for i in resp.json()["data"]["items"]}

    convo = items[str(ids["convo"])]
    assert convo["run_count"] == 2
    assert convo["error_count"] == 1
    assert convo["pending_count"] == 0
    assert convo["user_id"] == str(_USER_A)
    assert convo["title"] == "refund question"
    # tr-1 (100+20) + tr-2 (50+10) summed across the thread's runs.
    assert convo["tokens"]["input_tokens"] == 150
    assert convo["tokens"]["output_tokens"] == 30
    assert convo["tokens"]["total_tokens"] == 180
    assert convo["tokens"]["llm_calls"] == 2


@pytest.mark.asyncio
async def test_list_filters_by_agent(
    client_and_threads: tuple[AsyncClient, dict[str, UUID]],
) -> None:
    client, ids = client_and_threads
    resp = await client.get("/v1/conversations", params={"agent_name": "beta"})
    assert resp.status_code == 200
    got = {i["thread_id"] for i in resp.json()["data"]["items"]}
    assert got == {str(ids["other_agent"])}


@pytest.mark.asyncio
async def test_list_filters_by_user(
    client_and_threads: tuple[AsyncClient, dict[str, UUID]],
) -> None:
    client, ids = client_and_threads
    resp = await client.get("/v1/conversations", params={"user_id": str(_USER_B)})
    assert resp.status_code == 200
    got = {i["thread_id"] for i in resp.json()["data"]["items"]}
    assert got == {str(ids["other_user"])}


@pytest.mark.asyncio
async def test_list_version_without_agent_is_422(
    client_and_threads: tuple[AsyncClient, dict[str, UUID]],
) -> None:
    client, _ = client_and_threads
    resp = await client.get("/v1/conversations", params={"agent_version": "1.0.0"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_detail_returns_run_list_and_summary(
    client_and_threads: tuple[AsyncClient, dict[str, UUID]],
) -> None:
    client, ids = client_and_threads
    resp = await client.get(f"/v1/conversations/{ids['convo']}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["run_count"] == 2
    assert data["error_count"] == 1
    assert len(data["runs"]) == 2
    # Runs carry per-run token attribution + the error string.
    errored = [r for r in data["runs"] if r["status"] == "error"]
    assert errored and errored[0]["error"] == "boom"
    assert errored[0]["tokens"]["input_tokens"] == 50


@pytest.mark.asyncio
async def test_detail_unknown_thread_is_404(
    client_and_threads: tuple[AsyncClient, dict[str, UUID]],
) -> None:
    client, _ = client_and_threads
    resp = await client.get(f"/v1/conversations/{uuid4()}")
    assert resp.status_code == 404
