"""End-to-end tests for the real SSE run trigger (control-plane cutover).

The endpoint runs a real orchestrator graph in-process. To keep the
test off the network the injected :class:`AgentRuntime`'s builder
returns a :class:`BuiltAgent` over a *fake* ``LLMCaller`` — the
control-plane wiring (manifest load → run spawn → SSE drain) is what's
under test, not a real LLM call.

SSE vocabulary is ``metadata`` / ``updates`` / ``end`` (amended ADR B-4).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings
from helix_agent.persistence.audit_log import InMemoryAuditLogStore
from helix_agent.protocol import AuditQuery
from tests.agent_fixtures import stub_agent_runtime
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
async def runs_client(audit_store: InMemoryAuditLogStore) -> AsyncIterator[AsyncClient]:
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
        agent_runtime=stub_agent_runtime(),
    )
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {make_test_jwt(tenant_id=_DEFAULT_TENANT)}"}
    async with AsyncClient(
        transport=transport,
        base_url="http://control-plane.test",
        headers=headers,
    ) as client:
        await client.post("/v1/agents", json={"manifest_yaml": _AGENT_YAML})
        yield client


def _parse_sse(body: str) -> list[tuple[str, object]]:
    """Parse an SSE body into ``(event, data)`` pairs.

    Comment frames (``: heartbeat``) and any frame without a ``data:``
    line are skipped — only real events are returned.
    """
    events: list[tuple[str, object]] = []
    for chunk in body.split("\n\n"):
        if not chunk.strip():
            continue
        event_type = ""
        data_payload: str | None = None
        for line in chunk.splitlines():
            if line.startswith("event: "):
                event_type = line[len("event: ") :]
            elif line.startswith("data: "):
                data_payload = line[len("data: ") :]
        if data_payload is None:
            continue  # comment-only frame, e.g. ": heartbeat"
        events.append((event_type, json.loads(data_payload)))
    return events


async def _create_session(client: AsyncClient) -> str:
    response = await client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
    )
    assert response.status_code == 201
    return str(response.json()["data"]["thread_id"])


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_emits_metadata_updates_end(runs_client: AsyncClient) -> None:
    thread_id = await _create_session(runs_client)
    response = await runs_client.post(
        f"/v1/sessions/{thread_id}/runs", json={"input": "review the PR"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-helix-run-id"]

    events = _parse_sse(response.text)
    types = [e[0] for e in events]
    # Stream opens with metadata, closes with end, an updates in between.
    assert types[0] == "metadata"
    assert types[-1] == "end"
    assert "updates" in types

    metadata = events[0][1]
    assert isinstance(metadata, dict)
    assert metadata["thread_id"] == thread_id
    assert metadata["run_id"] == response.headers["x-helix-run-id"]


@pytest.mark.asyncio
async def test_run_streams_the_agent_reply(runs_client: AsyncClient) -> None:
    """The fake LLM's reply reaches the client inside an updates event."""
    thread_id = await _create_session(runs_client)
    response = await runs_client.post(f"/v1/sessions/{thread_id}/runs", json={"input": "hello"})
    assert response.status_code == 200
    body = response.text
    # The fake agent's content is carried in an updates chunk.
    assert "stub agent reply" in body


@pytest.mark.asyncio
async def test_run_emits_session_write_audit(
    runs_client: AsyncClient, audit_store: InMemoryAuditLogStore
) -> None:
    thread_id = await _create_session(runs_client)
    response = await runs_client.post(f"/v1/sessions/{thread_id}/runs", json={"input": "hi"})
    assert response.status_code == 200
    _ = response.text  # exhaust the stream

    page = await audit_store.query(AuditQuery(tenant_id=_DEFAULT_TENANT))
    run_events = [
        r
        for r in page.entries
        if r.action.value == "session:write"
        and r.resource_id == thread_id
        and r.details.get("stage") == "run.start"
    ]
    assert run_events, "expected a run.start audit row"
    assert run_events[0].details["input_len"] == 2


@pytest.mark.asyncio
async def test_run_emits_run_completed_audit(
    runs_client: AsyncClient, audit_store: InMemoryAuditLogStore
) -> None:
    """The run_agent worker writes a run:completed row at run end (F-3)."""
    thread_id = await _create_session(runs_client)
    response = await runs_client.post(f"/v1/sessions/{thread_id}/runs", json={"input": "hi"})
    assert response.status_code == 200
    _ = response.text  # exhaust the stream — the worker has finished by EOF

    page = await audit_store.query(AuditQuery(tenant_id=_DEFAULT_TENANT))
    completed = [r for r in page.entries if r.action.value == "run:completed"]
    assert len(completed) == 1
    entry = completed[0]
    assert entry.resource_id == thread_id
    assert entry.details["status"] == "success"
    assert entry.details["run_id"] == response.headers["x-helix-run-id"]


# ---------------------------------------------------------------------------
# error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_against_unknown_session_returns_404(
    runs_client: AsyncClient,
) -> None:
    response = await runs_client.post(
        "/v1/sessions/00000000-0000-0000-0000-000000000099/runs",
        json={},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_run_against_cancelled_session_returns_409(
    runs_client: AsyncClient,
) -> None:
    thread_id = await _create_session(runs_client)
    await runs_client.post(f"/v1/sessions/{thread_id}:cancel", json={})
    response = await runs_client.post(f"/v1/sessions/{thread_id}/runs", json={})
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_run_against_paused_session_returns_409(
    runs_client: AsyncClient,
) -> None:
    thread_id = await _create_session(runs_client)
    await runs_client.post(f"/v1/sessions/{thread_id}:pause", json={})
    response = await runs_client.post(f"/v1/sessions/{thread_id}/runs", json={})
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Stream B + E acceptance: create agent → session → run → real SSE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_acceptance_flow(runs_client: AsyncClient) -> None:
    """End-to-end happy path: HTTP run trigger → background graph →
    streamed SSE — the first M0 vertical slice."""
    agents = await runs_client.get("/v1/agents")
    assert agents.status_code == 200
    assert agents.json()["data"]["total"] == 1

    session_response = await runs_client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
    )
    assert session_response.status_code == 201
    thread_id = session_response.json()["data"]["thread_id"]

    run_response = await runs_client.post(
        f"/v1/sessions/{thread_id}/runs",
        json={"input": "review the PR"},
    )
    assert run_response.status_code == 200
    events = _parse_sse(run_response.text)
    types = [e[0] for e in events]
    assert types[0] == "metadata"
    assert types[-1] == "end"
    assert "updates" in types


# ---------------------------------------------------------------------------
# per-user isolation — Stream J.14
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_cannot_run_another_users_session(runs_client: AsyncClient) -> None:
    """A run trigger on another user's session is rejected (404)."""
    user_a = {
        "Authorization": "Bearer "
        + make_test_jwt(tenant_id=_DEFAULT_TENANT, subject="user-a", roles=("viewer",))
    }
    user_b = {
        "Authorization": "Bearer "
        + make_test_jwt(tenant_id=_DEFAULT_TENANT, subject="user-b", roles=("viewer",))
    }
    create = await runs_client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
        headers=user_a,
    )
    assert create.status_code == 201
    thread_id = create.json()["data"]["thread_id"]

    intruder = await runs_client.post(
        f"/v1/sessions/{thread_id}/runs", json={"input": "hi"}, headers=user_b
    )
    assert intruder.status_code == 404

    owner = await runs_client.post(
        f"/v1/sessions/{thread_id}/runs", json={"input": "hi"}, headers=user_a
    )
    assert owner.status_code == 200


# ---------------------------------------------------------------------------
# cross-tenant SSE isolation — Stream K.K2 (Mini-ADR K-2)
#
# SSE only flows out of POST /v1/sessions/{thread_id}/runs; there is no
# reconnect endpoint and run_id is server-generated uuid4. The route
# resolves the thread via threads.get(thread_id, tenant_id=jwt_tenant)
# at api/runs.py:191, which 404s when the thread does not belong to
# the caller's tenant. Mini-ADR K-2 commits NOT to add a duplicate
# SSE-layer guard; this test locks the invariant so any new SSE entry
# path must keep it.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runs_cross_tenant_sse_rejected(runs_client: AsyncClient) -> None:
    """A tenant B caller cannot stream a tenant A thread's runs."""
    from uuid import uuid4

    # Create the thread as tenant A (the default fixture tenant).
    create = await runs_client.post(
        "/v1/sessions",
        json={"agent_name": "code-reviewer", "agent_version": "1.0.0"},
    )
    assert create.status_code == 201
    tenant_a_thread_id = create.json()["data"]["thread_id"]

    # Mint a fresh tenant B JWT. The signature uses the same dev key
    # the verifier accepts, so the call is authenticated — the
    # rejection has to come from tenant isolation, not auth failure.
    tenant_b_id = uuid4()
    tenant_b_headers = {"Authorization": f"Bearer {make_test_jwt(tenant_id=tenant_b_id)}"}

    response = await runs_client.post(
        f"/v1/sessions/{tenant_a_thread_id}/runs",
        json={"input": "trying to peek"},
        headers=tenant_b_headers,
    )

    # 404 from the thread-ownership check — tenant B caller never
    # learns whether the thread exists.
    assert response.status_code == 404, response.text
    # SSE never started — the response body is JSON, not text/event-stream.
    # Regression where the stream opens before tenant check would flip
    # this assertion.
    assert "text/event-stream" not in response.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# GET run — Stream J.8 (Mini-ADR J-24)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_run_surfaces_pending_approval(runs_client: AsyncClient) -> None:
    """A run with an ``agent_approval`` row reports its pending approval."""
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from helix_agent.protocol import ApprovalRecord

    thread_id = await _create_session(runs_client)
    run_id = uuid4()
    # Seed a pending approval row directly into the in-memory store.
    app = runs_client._transport.app  # type: ignore[attr-defined,union-attr]
    now = datetime.now(UTC)
    await app.state.approval_store.create(
        ApprovalRecord(
            id=uuid4(),
            tenant_id=DEFAULT_DEV_TENANT_ID,
            run_id=run_id,
            thread_id=thread_id,  # type: ignore[arg-type]
            request_id="approval:seed",
            node="tools",
            reason_kind="policy_gate",
            action_summary="approval-gated tool 'send_email'",
            proposed_args={"to": "ops@example.com"},
            requested_at=now,
            timeout_at=now + timedelta(hours=24),
        )
    )

    resp = await runs_client.get(f"/v1/sessions/{thread_id}/runs/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    pending = body["pending_approval"]
    assert pending is not None
    assert pending["reason_kind"] == "policy_gate"
    assert pending["action_summary"] == "approval-gated tool 'send_email'"
    assert pending["proposed_args"] == {"to": "ops@example.com"}


@pytest.mark.asyncio
async def test_get_run_unknown_returns_404(runs_client: AsyncClient) -> None:
    """An unknown run id on a known thread — no approval, not in RunManager."""
    from uuid import uuid4

    thread_id = await _create_session(runs_client)
    resp = await runs_client.get(f"/v1/sessions/{thread_id}/runs/{uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_run_cross_tenant_returns_404(runs_client: AsyncClient) -> None:
    """A tenant B caller cannot read a tenant A thread's run."""
    from uuid import uuid4

    thread_id = await _create_session(runs_client)
    tenant_b_headers = {"Authorization": f"Bearer {make_test_jwt(tenant_id=uuid4())}"}
    resp = await runs_client.get(
        f"/v1/sessions/{thread_id}/runs/{uuid4()}", headers=tenant_b_headers
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST resume — Stream J.8-step3b (Mini-ADR J-24)
# ---------------------------------------------------------------------------


async def _seed_pending_approval(
    client: AsyncClient,
    thread_id: str,
    *,
    status: str = "pending",
) -> UUID:
    """Seed an ``agent_approval`` row directly into the in-memory store."""
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from helix_agent.protocol import ApprovalRecord, ApprovalStatus

    run_id = uuid4()
    app = client._transport.app  # type: ignore[attr-defined,union-attr]
    now = datetime.now(UTC)
    rec = ApprovalRecord(
        id=uuid4(),
        tenant_id=DEFAULT_DEV_TENANT_ID,
        run_id=run_id,
        thread_id=UUID(thread_id),
        request_id="approval:seed",
        node="tools",
        reason_kind="policy_gate",
        action_summary="approval-gated tool 'send_email'",
        proposed_args={"to": "ops@example.com"},
        requested_at=now,
        timeout_at=now + timedelta(hours=24),
        status=ApprovalStatus(status),
    )
    await app.state.approval_store.create(rec)
    return run_id


@pytest.mark.asyncio
async def test_resume_unknown_run_returns_404(runs_client: AsyncClient) -> None:
    from uuid import uuid4

    thread_id = await _create_session(runs_client)
    resp = await runs_client.post(
        f"/v1/sessions/{thread_id}/runs/{uuid4()}/resume",
        json={"decision": "approve"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_resume_already_decided_returns_409(runs_client: AsyncClient) -> None:
    thread_id = await _create_session(runs_client)
    run_id = await _seed_pending_approval(runs_client, thread_id, status="approved")
    resp = await runs_client.post(
        f"/v1/sessions/{thread_id}/runs/{run_id}/resume",
        json={"decision": "approve"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_resume_modify_without_args_returns_422(runs_client: AsyncClient) -> None:
    thread_id = await _create_session(runs_client)
    run_id = await _seed_pending_approval(runs_client, thread_id)
    resp = await runs_client.post(
        f"/v1/sessions/{thread_id}/runs/{run_id}/resume",
        json={"decision": "modify"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_resume_cross_tenant_returns_404(runs_client: AsyncClient) -> None:
    from uuid import uuid4

    thread_id = await _create_session(runs_client)
    run_id = await _seed_pending_approval(runs_client, thread_id)
    tenant_b_headers = {"Authorization": f"Bearer {make_test_jwt(tenant_id=uuid4())}"}
    resp = await runs_client.post(
        f"/v1/sessions/{thread_id}/runs/{run_id}/resume",
        json={"decision": "approve"},
        headers=tenant_b_headers,
    )
    assert resp.status_code == 404
