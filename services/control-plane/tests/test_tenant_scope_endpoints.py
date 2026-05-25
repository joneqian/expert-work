"""Stream N — end-to-end tests for 5 list endpoints integrating ``ensure_tenant_scope``.

Confirms the wiring: each endpoint accepts ``?tenant_id=`` and routes
to ``list_all_tenants`` / ``list_by_tenant`` based on the resolution.
Decision matrix is exhaustively covered in :mod:`test_tenant_scope`;
this file proves the 5 routers + 5 stores actually use it.

Matrix per endpoint:
  - tenant_admin without ``?tenant_id``     → 200, items from home tenant, cross_tenant=False
  - tenant_admin with ``?tenant_id=*``      → 403 CROSS_TENANT_FORBIDDEN
  - system_admin with ``?tenant_id=*``      → 200, items from all tenants, cross_tenant=True
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane.app import create_app
from control_plane.audit import build_default_audit_logger
from control_plane.settings import Settings
from helix_agent.persistence.audit_log import InMemoryAuditLogStore
from helix_agent.protocol import (
    AgentMetadata,
    AgentSpec,
    AgentSpecBody,
    CandidateStatus,
    CurationCandidateRecord,
    EvalDatasetRecord,
    FilesystemSpec,
    MemorySpec,
    ModelSpec,
    NetworkSpec,
    ResourceSpec,
    Role,
    SandboxSpec,
    SystemPromptSpec,
    TenantConfig,
    TriggerRecord,
)
from tests.auth_fixtures import TEST_AUDIENCE, TEST_ISSUER, build_test_jwt_verifier, make_test_jwt

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TENANT_A = uuid4()
_TENANT_B = uuid4()


def _make_agent_spec(name: str) -> AgentSpec:
    return AgentSpec(
        apiVersion="helix.io/v1",
        kind="Agent",
        metadata=AgentMetadata(name=name, version="1.0.0", tenant="t"),
        spec=AgentSpecBody(
            tenant_config=TenantConfig(),
            model=ModelSpec(provider="anthropic", name="claude-sonnet-4-5"),
            system_prompt=SystemPromptSpec(template="x"),
            sandbox=SandboxSpec(
                resources=ResourceSpec(cpu="1.0", memory="1Gi"),
                network=NetworkSpec(egress="proxy", allowlist=["api.anthropic.com"]),
                filesystem=FilesystemSpec(readonly_root=True, writable=["/workspace"]),
            ),
            memory=MemorySpec(),
        ),
    )


@pytest.fixture
async def app_state() -> AsyncIterator[tuple[AsyncClient, UUID]]:
    """App + a system-admin user pre-seeded with platform binding.

    Yields ``(client, system_admin_user_id)`` so each test can issue both
    tenant-admin and system-admin JWTs against the same app.
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

    # Seed cross-tenant rows in 5 stores. Done via direct app.state access
    # because we want known UUIDs to assert against.
    now = datetime.now(UTC)
    repo = app.state.agent_spec_repo
    for tenant in (_TENANT_A, _TENANT_B):
        await repo.create(
            tenant_id=tenant,
            spec=_make_agent_spec(f"agent-in-{tenant.hex[:6]}"),
            spec_sha256="x" * 64,
            created_by="seed",
        )

    skills = app.state.skill_store
    for tenant in (_TENANT_A, _TENANT_B):
        await skills.create_skill(
            skill_id=uuid4(),
            tenant_id=tenant,
            name=f"skill-{tenant.hex[:6]}",
            description="",
            category="test",
        )

    triggers = app.state.trigger_store
    for tenant in (_TENANT_A, _TENANT_B):
        await triggers.create(
            TriggerRecord(
                id=uuid4(),
                tenant_id=tenant,
                user_id=None,
                agent_name=f"agent-in-{tenant.hex[:6]}",
                agent_version="1.0.0",
                name="t1",
                kind="cron",
                config={"expr": "* * * * *"},
                enabled=True,
                source="api",
                webhook_secret_hash=None,
                created_at=now,
                updated_at=now,
            )
        )

    candidates = app.state.curation_candidate_store
    for tenant in (_TENANT_A, _TENANT_B):
        await candidates.upsert(
            CurationCandidateRecord(
                id=uuid4(),
                tenant_id=tenant,
                agent_name=f"agent-in-{tenant.hex[:6]}",
                agent_version="1.0.0",
                thread_id=uuid4(),
                user_id=None,
                trajectory_key=f"k/{tenant.hex}",
                outcome="failed",
                signal="failed_outcome",
                feedback_rating=None,
                status=CandidateStatus.PENDING,
                eval_dataset_id=None,
                detected_at=now,
                reviewed_at=None,
            )
        )

    datasets = app.state.eval_dataset_store
    for tenant in (_TENANT_A, _TENANT_B):
        await datasets.create(
            EvalDatasetRecord(
                id=uuid4(),
                tenant_id=tenant,
                agent_name=f"agent-in-{tenant.hex[:6]}",
                name="case-1",
                input={},
                expected={"x": 1},
                source="trajectory",
                source_trajectory_key=None,
                source_user_id=None,
                created_at=now,
                updated_at=now,
            )
        )

    # System-admin user pre-seeded with platform-scope binding.
    sys_admin_id = uuid4()
    await app.state.role_binding_repo.create(
        subject_type="user",
        subject_id=sys_admin_id,
        tenant_id=None,
        role=Role.SYSTEM_ADMIN,
        platform_scope=True,
        granted_by="seed",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://control-plane.test") as client:
        yield client, sys_admin_id


def _tenant_admin_token() -> str:
    return make_test_jwt(tenant_id=_TENANT_A, subject=str(uuid4()))


def _system_admin_token(sys_admin_id: UUID) -> str:
    return make_test_jwt(tenant_id=_TENANT_A, subject=str(sys_admin_id))


# ---------------------------------------------------------------------------
# Per-endpoint matrix — each test runs the 3-row matrix:
#   1. tenant_admin home   → 200, 1 item (their tenant), cross_tenant=False
#   2. tenant_admin "*"    → 403 CROSS_TENANT_FORBIDDEN
#   3. system_admin "*"    → 200, 2 items (both tenants), cross_tenant=True
# ---------------------------------------------------------------------------


_ENDPOINTS: list[tuple[str, str]] = [
    ("agents", "/v1/agents"),
    ("skills", "/v1/skills"),
    ("triggers", "/v1/triggers"),
    ("curation", "/v1/curation/candidates"),
    ("eval-datasets", "/v1/eval-datasets"),
]


def _items(body: dict[str, object]) -> list[dict[str, object]]:
    """Some endpoints wrap items in ``data``, others put items at the top level."""
    if "data" in body and isinstance(body["data"], dict):
        data = body["data"]
        return list(data.get("items", []))  # type: ignore[arg-type]
    return list(body.get("items", []))  # type: ignore[arg-type]


def _is_cross_tenant(body: dict[str, object]) -> bool:
    if "data" in body and isinstance(body["data"], dict):
        return bool(body["data"].get("cross_tenant"))  # type: ignore[union-attr]
    return bool(body.get("cross_tenant"))


@pytest.mark.parametrize("name,path", _ENDPOINTS)
@pytest.mark.asyncio
async def test_tenant_admin_home_tenant_returns_only_home_rows(
    app_state: tuple[AsyncClient, UUID], name: str, path: str
) -> None:
    client, _ = app_state
    headers = {"Authorization": f"Bearer {_tenant_admin_token()}"}
    response = await client.get(path, headers=headers)
    assert response.status_code == 200, f"{name}: {response.text}"
    body = response.json()
    assert _is_cross_tenant(body) is False
    items = _items(body)
    # Seeded 1 row per tenant; home tenant_admin sees exactly 1.
    assert len(items) == 1, f"{name}: expected 1, got {len(items)} — {items}"


@pytest.mark.parametrize("name,path", _ENDPOINTS)
@pytest.mark.asyncio
async def test_tenant_admin_star_tenant_id_returns_403(
    app_state: tuple[AsyncClient, UUID], name: str, path: str
) -> None:
    client, _ = app_state
    headers = {"Authorization": f"Bearer {_tenant_admin_token()}"}
    response = await client.get(f"{path}?tenant_id=*", headers=headers)
    assert response.status_code == 403, f"{name}: {response.status_code} {response.text}"
    body = response.json()
    detail = body.get("detail") or body.get("error", {})
    # FastAPI HTTPException(detail=dict) puts dict under "detail"; check either shape.
    if isinstance(detail, dict):
        assert detail.get("code") == "CROSS_TENANT_FORBIDDEN", f"{name}: {detail}"


@pytest.mark.parametrize("name,path", _ENDPOINTS)
@pytest.mark.asyncio
async def test_system_admin_star_tenant_id_returns_all_tenants(
    app_state: tuple[AsyncClient, UUID], name: str, path: str
) -> None:
    client, sys_admin_id = app_state
    headers = {"Authorization": f"Bearer {_system_admin_token(sys_admin_id)}"}
    response = await client.get(f"{path}?tenant_id=*", headers=headers)
    assert response.status_code == 200, f"{name}: {response.text}"
    body = response.json()
    assert _is_cross_tenant(body) is True, f"{name}: cross_tenant flag missing"
    items = _items(body)
    # Seeded 2 tenants, 1 row each.
    assert len(items) == 2, f"{name}: expected 2 rows across tenants, got {len(items)}"
