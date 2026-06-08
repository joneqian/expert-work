"""E2E tests for ``/v1/skill-evolution`` admin API — Stream SE (SE-8-2).

Covers the promote-approval flow (request / review-queue / approve→visibility
flip / reject), eval-evidence + lineage reads, and audit emission. Seeds
agent_private skills directly via the app's in-memory ``SkillStore`` (the public
``POST /v1/skills`` only makes tenant-visible drafts).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import DEFAULT_DEV_TENANT_ID, Settings
from helix_agent.persistence.audit_log import InMemoryAuditLogStore
from helix_agent.protocol import AuditAction, AuditQuery, SkillEvalResult
from tests.auth_fixtures import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    build_test_jwt_verifier,
    make_test_jwt,
)

_TENANT = DEFAULT_DEV_TENANT_ID
_ADMIN = uuid4()  # UUID subject so the decider is a real user id


def _settings() -> Settings:
    return Settings(
        env="dev",
        auth_mode="dev",
        rate_limit_burst=10_000,
        rate_limit_per_second=10_000.0,
        oidc_issuer=TEST_ISSUER,
        oidc_audience=[TEST_AUDIENCE],
    )


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {make_test_jwt(tenant_id=_TENANT, subject=str(_ADMIN))}"}


Setup = tuple[AsyncClient, FastAPI, InMemoryAuditLogStore]


@pytest.fixture
async def setup() -> AsyncIterator[Setup]:
    audit_store = InMemoryAuditLogStore()
    app = create_app(
        settings=_settings(),
        audit_logger=build_default_audit_logger(audit_store),
        jwt_verifier=build_test_jwt_verifier(),
        enable_reaper=False,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://cp.test", headers=_headers()
    ) as client:
        yield client, app, audit_store


async def _seed_agent_private(app: FastAPI, *, name: str) -> str:
    """Create an agent_private skill (v1) in the app store; return its id."""
    store = app.state.skill_store
    skill_id = uuid4()
    await store.create_skill(
        skill_id=skill_id,
        tenant_id=_TENANT,
        name=name,
        visibility="agent_private",
        created_by_user_id=_ADMIN,
        created_by_agent_name="researcher",
    )
    await store.add_version(
        version_id=uuid4(),
        skill_id=skill_id,
        tenant_id=_TENANT,
        prompt_fragment="do the thing",
        authored_by="agent",
        evolution_origin="in_session",
    )
    return str(skill_id)


@pytest.mark.asyncio
async def test_request_review_approve_flow(setup: Setup) -> None:
    client, app, audit_store = setup
    sid = await _seed_agent_private(app, name=f"skill-{uuid4().hex[:8]}")

    # open a promote request
    r = await client.post(
        f"/v1/skill-evolution/skills/{sid}/promote-requests",
        json={"skill_version": 1, "reason": "tenant-wide useful"},
    )
    assert r.status_code == 201, r.text
    req = r.json()
    assert req["status"] == "pending"
    rid = req["id"]

    # it shows up in the review queue
    q = await client.get("/v1/skill-evolution/promote-requests", params={"status": "pending"})
    assert q.status_code == 200
    assert rid in [x["id"] for x in q.json()["items"]]

    # approve → status approved + skill visibility flips to tenant
    a = await client.post(f"/v1/skill-evolution/promote-requests/{rid}/approve", json={})
    assert a.status_code == 200, a.text
    assert a.json()["status"] == "approved"

    skill = (await client.get(f"/v1/skills/{sid}")).json()
    assert skill["visibility"] == "tenant"

    # audit row for the approval
    entries = await audit_store.query(AuditQuery(tenant_id=_TENANT))
    actions = {e.action for e in entries.entries}
    assert AuditAction.SKILL_PROMOTE_REQUESTED in actions
    assert AuditAction.SKILL_PROMOTE_APPROVED in actions


@pytest.mark.asyncio
async def test_reject_keeps_agent_private(setup: Setup) -> None:
    client, app, _ = setup
    sid = await _seed_agent_private(app, name=f"skill-{uuid4().hex[:8]}")
    r = await client.post(
        f"/v1/skill-evolution/skills/{sid}/promote-requests", json={"skill_version": 1}
    )
    rid = r.json()["id"]
    rej = await client.post(
        f"/v1/skill-evolution/promote-requests/{rid}/reject",
        json={"decision_reason": "too narrow"},
    )
    assert rej.status_code == 200
    assert rej.json()["status"] == "rejected"
    skill = (await client.get(f"/v1/skills/{sid}")).json()
    assert skill["visibility"] == "agent_private"


@pytest.mark.asyncio
async def test_duplicate_pending_409(setup: Setup) -> None:
    client, app, _ = setup
    sid = await _seed_agent_private(app, name=f"skill-{uuid4().hex[:8]}")
    body = {"skill_version": 1}
    assert (
        await client.post(f"/v1/skill-evolution/skills/{sid}/promote-requests", json=body)
    ).status_code == 201
    dup = await client.post(f"/v1/skill-evolution/skills/{sid}/promote-requests", json=body)
    assert dup.status_code == 409


@pytest.mark.asyncio
async def test_request_unknown_skill_404(setup: Setup) -> None:
    client, _, _ = setup
    r = await client.post(
        f"/v1/skill-evolution/skills/{uuid4()}/promote-requests", json={"skill_version": 1}
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_approve_unknown_request_404(setup: Setup) -> None:
    client, _, _ = setup
    r = await client.post(f"/v1/skill-evolution/promote-requests/{uuid4()}/approve", json={})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_eval_results_read(setup: Setup) -> None:
    client, app, _ = setup
    sid = await _seed_agent_private(app, name=f"skill-{uuid4().hex[:8]}")
    await app.state.skill_store.record_eval_result(
        result=SkillEvalResult(
            id=uuid4(),
            tenant_id=_TENANT,
            skill_id=sid,  # type: ignore[arg-type]
            skill_version=1,
            baseline_score=0.4,
            skill_score=0.85,
            delta=0.45,
            n_cases=12,
            replay_source="trajectory",
            verdict="pass",
            created_at=datetime.now(UTC),
        )
    )
    r = await client.get(f"/v1/skill-evolution/skills/{sid}/eval-results")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["verdict"] == "pass"
    assert items[0]["delta"] == pytest.approx(0.45)


@pytest.mark.asyncio
async def test_lineage_read(setup: Setup) -> None:
    client, app, _ = setup
    sid = await _seed_agent_private(app, name=f"skill-{uuid4().hex[:8]}")
    r = await client.get(f"/v1/skill-evolution/skills/{sid}/lineage")
    assert r.status_code == 200
    body = r.json()
    assert body["skill"]["id"] == sid
    assert body["forked_from_source"] is None
    assert len(body["versions"]) == 1
    assert body["versions"][0]["evolution_origin"] == "in_session"


@pytest.mark.asyncio
async def test_lineage_unknown_skill_404(setup: Setup) -> None:
    client, _, _ = setup
    r = await client.get(f"/v1/skill-evolution/skills/{uuid4()}/lineage")
    assert r.status_code == 404
